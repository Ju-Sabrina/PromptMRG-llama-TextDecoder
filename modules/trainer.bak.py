import os
from abc import abstractmethod

import time
import torch
import torch.distributed as dist
import pandas as pd
import numpy as np
from numpy import inf
from .metrics_clinical import CheXbertMetrics
import copy
from .optims import LinearWarmupCosineLRScheduler
from bitsandbytes.optim import PagedAdamW32bit  # ✅ 添加到顶部 import 部分




class BaseTrainer(object):
    def __init__(self, model, criterion_cls, base_probs, metric_ftns, args, device, is_main_process):
        self.args = args
        self.model = model
        self.device = device
        self.is_main_process = is_main_process

        self.chexbert_metrics = CheXbertMetrics('/home/fzu/jusibo/dataset/reproduce/PromptMRG/checkpoints/stanford/chexbert/chexbert.pth', args.batch_size, device)

        self.criterion_cls = criterion_cls
        self.base_probs = base_probs
        self.metric_ftns = metric_ftns
        #################
        self.optimizer = None
        num_parameters = 0
        p_wd, p_non_wd = [], []
        for n, p in self.model.named_parameters():
            if not p.requires_grad:
                continue  # frozen weights
            if p.ndim < 2 or "bias" in n or "ln" in n or "bn" in n:
                p_non_wd.append(p)
            else:
                p_wd.append(p)
            num_parameters += p.data.nelement()
        print("number of trainable parameters: {}".format(num_parameters))
        optim_params = [
            {
                "params": p_wd,
                "weight_decay": float(self.args.weight_decay),
            },
            {"params": p_non_wd, "weight_decay": 0},
        ]
        beta2 = 0.999
        # ✅ 替换 AdamW 为节省显存的版本
        self.optimizer = PagedAdamW32bit(
            optim_params,
            lr=float(self.args.init_lr),
            betas=(0.9, beta2)
        )
        #################

        self.epochs = self.args.epochs

        self.mnt_metric = 'val_' + args.monitor_metric

        self.mnt_best = 0 
        self.log_best = {}

        self.start_epoch = 1
        self.checkpoint_dir = args.save_dir

        if not os.path.exists(self.checkpoint_dir):
            os.makedirs(self.checkpoint_dir)

        # ✅ 截断 base_probs 保证与 num_labels 一致（避免 18维 → 14维 mismatch）
        if hasattr(self.model, 'module'):
            num_labels = self.model.module.num_labels
        else:
            num_labels = self.model.num_labels

        if isinstance(self.base_probs, np.ndarray) and self.base_probs.shape[0] > num_labels:
            # print(f"[DEBUG] base_probs shape = {self.base_probs.shape}, truncating to {num_labels}")
            self.base_probs = self.base_probs[:num_labels]


    @abstractmethod
    def _train_epoch(self, epoch):
        raise NotImplementedError

    def train(self):
        for epoch in range(self.start_epoch, self.epochs + 1):
            if self.args.distributed:
                # for different shuffling
                self.train_dataloader.sampler.set_epoch(epoch)

            result = self._train_epoch_blip(epoch)
            if self.args.distributed and dist.is_available() and dist.is_initialized():
                dist.barrier()
            result = self.eval_blip(result)

            # save logged information 
            log = {'epoch': epoch}
            log.update(result)

            # record best
            if self.is_main_process:
                if log[self.mnt_metric] >= self.mnt_best:
                    self.mnt_best = log[self.mnt_metric]
                    self.log_best = copy.deepcopy(log)
                    best_path = os.path.join(self.checkpoint_dir, 'model_best.pth')
                    torch.save(self.model.module.state_dict(), best_path)
                    print("Saving current best to {}".format(best_path))

            # print logged information 
            for key, value in log.items():
                print('\t{:15s}: {}'.format(str(key), value))

        if self.is_main_process:
            print('Best results w.r.t {}:'.format(self.mnt_metric))
            for key, value in self.log_best.items():
                print('\t{:15s}: {}'.format(str(key), value))

class Trainer(BaseTrainer):
    def __init__(self, model, criterion_cls, base_probs, metric_ftns, args, train_dataloader, val_dataloader, test_dataloader, device, is_main_process):
        super(Trainer, self).__init__(model, criterion_cls, base_probs, metric_ftns, args, device, is_main_process)
        self.train_dataloader = train_dataloader
        self.val_dataloader = val_dataloader
        self.test_dataloader = test_dataloader
        self.lr_scheduler = LinearWarmupCosineLRScheduler(
            self.optimizer, 
            self.args.epochs, 
            self.args.min_lr, 
            self.args.init_lr, 
            decay_rate=None, 
            warmup_start_lr=self.args.warmup_lr,
            warmup_steps=self.args.warmup_steps,
        )

    def _train_epoch_blip(self, epoch):
        train_loss = 0
        self.model.train()
        for batch_idx, (images, captions, cls_labels, clip_memory) in enumerate(self.train_dataloader):
            images = images.to(self.device)
            cls_labels = cls_labels.to(self.device)
            clip_memory = clip_memory.to(self.device)
            self.lr_scheduler.step(cur_epoch=epoch, cur_step=batch_idx)
            loss_lm, loss_cls = self.model(images, captions, cls_labels, clip_memory, self.criterion_cls, self.base_probs)
            loss = loss_lm + self.args.cls_weight*loss_cls
            if batch_idx%10 == 0:
                print("{}/{} loss: {}, loss_lm: {}, loss_cls: {}".format(batch_idx, len(self.train_dataloader), loss.item(), loss_lm.item(), self.args.cls_weight*loss_cls.item()))
            train_loss += loss.item()
            loss.backward()
            torch.nn.utils.clip_grad_value_(self.model.parameters(), 0.1)
            self.optimizer.step()
            self.optimizer.zero_grad()
        log = {'train_loss': train_loss / len(self.train_dataloader)}

        return log

    def eval_blip(self, log):
        model_to_eval = self.model.module if hasattr(self.model, 'module') else self.model
        model_to_eval.eval()

        print("🔍 开始验证阶段 eval_blip() ...")
        t_start = time.time()

        logits = []
        counts = []
        empty_val_ids = []

        with torch.no_grad():
            val_gts, val_res = [], []
            for batch_idx, (images_id, images, captions, cls_labels, clip_memory) in enumerate(self.val_dataloader):
                print(f"📦 [VAL] batch {batch_idx} loaded")
                t1 = time.time()

                images = images.to(self.device) 
                cls_labels = cls_labels.to(self.device)
                clip_memory = clip_memory.to(self.device)

                print("🧠 调用 generate() 前")
                reports, cls_preds, cls_preds_logits = model_to_eval.generate(
                    images, clip_memory,
                    sample=False,
                    num_beams=self.args.beam_size,
                    max_length=self.args.gen_max_len,
                    min_length=self.args.gen_min_len
                )
                print(f"✅ generate() 完成，用时 {time.time() - t1:.2f}s")

                # 处理 classification logits
                cls_labels = (cls_labels == 1).float()
                if cls_labels.shape[-1] > cls_preds_logits.shape[-1]:
                    cls_labels = cls_labels[:, :cls_preds_logits.shape[-1]]

                logit = cls_preds_logits * cls_labels
                logits.append(logit.cpu().numpy())
                counts.append(cls_labels.cpu().numpy())

                # ✅ 替换空字符串 + 打印 + 收集 image_id
                for ridx, report in enumerate(reports):
                    if report.strip() == "":
                        fixed_id = images_id[ridx]
                        print(f"[Fix][VAL] Empty report detected at image_id: {fixed_id}")
                        empty_val_ids.append(fixed_id)
                        reports[ridx] = "[EMPTY]"

                val_res.extend(reports)
                val_gts.extend(captions)

            # ✅ 保存空样本 ID 到文件
            if len(empty_val_ids) > 0:
                with open("empty_val_reports.txt", "w") as f:
                    for eid in empty_val_ids:
                        f.write(str(eid) + "\n")
                print(f"[VAL] Empty reports written to empty_val_reports.txt")

            print("📊 聚合 logits")
            logits = np.concatenate(logits, axis=0)
            counts = np.concatenate(counts, axis=0)
            logits = logits.sum(0) / counts.sum(0)
            logits /= np.max(logits)

            if logits.shape[0] > model_to_eval.num_labels:
                logits = logits[:model_to_eval.num_labels]
            self.base_probs = logits

            print("📏 计算验证指标 metric_ftns")
            val_met = self.metric_ftns({i: [gt] for i, gt in enumerate(val_gts)},
                                    {i: [re] for i, re in enumerate(val_res)})
            val_ce = self.chexbert_metrics.compute(val_gts, val_res)
            log.update(**{'val_' + k: v for k, v in val_met.items()})
            log.update(**{'val_' + k: v for k, v in val_ce.items()})

        print("🧪 开始测试集阶段 (test set)")
        empty_test_ids = []

        with torch.no_grad():
            test_gts, test_res = [], []
            for batch_idx, (images_id, images, captions, cls_labels, clip_memory) in enumerate(self.test_dataloader):
                print(f"📦 [TEST] batch {batch_idx} loaded")
                images = images.to(self.device)
                clip_memory = clip_memory.to(self.device)

                reports, _, _ = model_to_eval.generate(
                    images, clip_memory,
                    sample=False,
                    num_beams=self.args.beam_size,
                    max_length=self.args.gen_max_len,
                    min_length=self.args.gen_min_len
                )

                # ✅ 替换空字符串 + 打印 + 收集 image_id
                for ridx, report in enumerate(reports):
                    if report.strip() == "":
                        fixed_id = images_id[ridx]
                        print(f"[Fix][TEST] Empty report detected at image_id: {fixed_id}")
                        empty_test_ids.append(fixed_id)
                        reports[ridx] = "[EMPTY]"

                test_res.extend(reports)
                test_gts.extend(captions)

            # ✅ 保存空样本 ID 到文件
            if len(empty_test_ids) > 0:
                with open("empty_test_reports.txt", "w") as f:
                    for eid in empty_test_ids:
                        f.write(str(eid) + "\n")
                print(f"[TEST] Empty reports written to empty_test_reports.txt")

            test_met = self.metric_ftns({i: [gt] for i, gt in enumerate(test_gts)},
                                        {i: [re] for i, re in enumerate(test_res)})
            test_ce = self.chexbert_metrics.compute(test_gts, test_res)
            log.update(**{'test_' + k: v for k, v in test_met.items()})
            log.update(**{'test_' + k: v for k, v in test_ce.items()})

        t_end = time.time()
        print(f"✅ eval_blip 完成，总耗时 {t_end - t_start:.2f}s")

        return log



    
