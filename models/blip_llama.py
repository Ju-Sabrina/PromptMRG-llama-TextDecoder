import os
import warnings
warnings.filterwarnings("ignore")
import torch
from torch import nn
import torch.nn.functional as F
import time
from transformers import LlamaForCausalLM, LlamaTokenizer
from models.resnet import blip_resnet
from models.transformer import Transformer
from peft import prepare_model_for_kbit_training, get_peft_model, LoraConfig
from itertools import groupby

CHEXPERT_LABELS_EN = [
    'enlarged cardiomediastinum', 'cardiomegaly', 'lung opacity', 'lung lesion',
    'edema', 'consolidation', 'pneumonia', 'atelectasis', 'pneumothorax',
    'pleural effusion', 'pleural other', 'fracture', 'support devices', 'no finding'
]

# 标签索引到文本状态映射:
# 0 -> not mentioned (未提及)
# 1 -> positive (阳性发现)
# 2 -> negative (阴性发现)
# 3 -> uncertain (不确定)
LABEL_MAP = {0: 'not mentioned', 1: 'positive', 2: 'negative', 3: 'uncertain'}

# 根据分类预测构建 [INST] 提示
# 输出示例: [INST] There is a positive finding of pneumonia. No evidence of edema. Please write a comprehensive and medically accurate radiology report. [/INST]
def build_prompt_from_cls_preds_en(cls_preds):
    """
    根据分类预测构建诊断提示。
    输入: cls_preds: List[List[int]] 每个元素是长度为14的标签状态索引列表
    输出: prompts: List[str] 包含每个样本的 [INST] 提示
    """
    prompts = []
    for labels in cls_preds:
        # 根据 LABEL_MAP 映射状态
        pos_list, neg_list, unc_list = [], [], []
        for i, state in enumerate(labels):
            label = CHEXPERT_LABELS_EN[i]
            status = LABEL_MAP[state]
            if state == 1:
                pos_list.append(label)
            elif state == 2:
                neg_list.append(label)
            elif state == 3:
                unc_list.append(label)
        # 构建英文自然语言短语
        def join_labels(lst):
            if len(lst) == 1:
                return lst[0]
            if len(lst) == 2:
                return f"{lst[0]} and {lst[1]}"
            return ", ".join(lst[:-1]) + f", and {lst[-1]}"
        sentences = []
        if pos_list:
            sentences.append(f"There is a positive finding of {join_labels(pos_list)}.")
        if neg_list:
            sentences.append(f"No evidence of {join_labels(neg_list)}.")
        if unc_list:
            sentences.append(f"The presence of {join_labels(unc_list)} is uncertain.")
        findings = " ".join(sentences) if sentences else "No significant abnormalities are observed."
        # 最终拼接成指令格式
        prompt = (
            f"[INST] {findings} "
            f"Please write a comprehensive and medically accurate radiology report."
            f" [/INST]"
        )
        prompts.append(prompt[:512])
    return prompts



class BLIP_Decoder(nn.Module):
    def __init__(self, args):
        super().__init__()
        # 模型参数
        self.num_labels = 14
        vision_width = 2048
        llama_hidden = 4096
        
        # 1. 可学习的模态嵌入：0→图像前缀，1→文本
        self.modality_embed = nn.Embedding(2, llama_hidden)
        # 2. 视觉前缀独立位置编码：
        # 假设你最大会拼接 N_vis 张 patch，或者直接用图像特征序列长度
        N_vis = args.max_image_patches  # e.g. 196
        self.vis_pos_embed = nn.Parameter(torch.zeros(1, N_vis, llama_hidden))
        nn.init.normal_(self.vis_pos_embed, std=0.02)

        # 基础组件
        self.visual_encoder = blip_resnet(args)
        self.vision_proj_lm = nn.Linear(vision_width, llama_hidden)
        self.vision_proj_mem = nn.Linear(vision_width, 512)
        self.memory = Transformer(d_model=512, num_encoder_layers=2, num_decoder_layers=2, num_queries=1)
        # 分类头
        self.cls_head = nn.Linear(vision_width + 512, self.num_labels * 4)
        nn.init.normal_(self.cls_head.weight, std=0.001)
        nn.init.constant_(self.cls_head.bias, 0)
        # LLaMA 加载
        self.tokenizer = LlamaTokenizer.from_pretrained(args.llama_path, use_fast=False)
        self.tokenizer.pad_token = self.tokenizer.eos_token
        self.llama = LlamaForCausalLM.from_pretrained(
            args.llama_path, device_map="auto", torch_dtype=torch.float16
        )
        self.llama.gradient_checkpointing_enable()
        self.llama.config.use_cache = False
        self.llama = prepare_model_for_kbit_training(self.llama)
        if getattr(args, 'use_lora', False):
            lora_cfg = LoraConfig(r=8, lora_alpha=16, target_modules=["q_proj","v_proj"], lora_dropout=0.05, bias="none", task_type="CAUSAL_LM")
            self.llama = get_peft_model(self.llama, lora_cfg)
        self.embed_tokens = self.llama.get_input_embeddings()
        # 设备/dtype
        self.device = next(self.llama.parameters()).device
        self.dtype = next(self.llama.parameters()).dtype
        self.base_probs = None

    def encode_visual(self, image):
        """获取视觉特征与投影"""
        """
        Args:
            image: Tensor of shape [B, C, H, W]
        Returns:
            img_feats: [B, N_vis, 2048]
            proj_feats: [B, N_vis, llama_hidden]
            avg_feats: [B, 2048]
        """
        img_feats, avg_feats = self.visual_encoder(image)
        proj_feats = self.vision_proj_lm(img_feats)
    return img_feats, proj_feats, avg_feats

    def construct_inputs(self, visual_embeds, prompts):
        """
        visual_embeds: [B, N_vis, llama_hidden]
        prompts      : List[str] → batch of text prompts
        """
        B, N_vis, H = visual_embeds.size()
        # ——— 文本部分嵌入 ———
        tokens = self.tokenizer(prompts,
                                return_tensors='pt',
                                padding=True,
                                truncation=True,
                                max_length=128).to(self.device)
        txt_embeds = self.embed_tokens(tokens.input_ids)        # [B, N_txt, H]
        N_txt = txt_embeds.size(1)

        # ——— 位置 + 模态 嵌入叠加 ———
        # 1) 给视觉前缀加独立位置编码
        vis_pos = self.vis_pos_embed[:, :N_vis, :]             # [1, N_vis, H]
        visual_embeds = visual_embeds + vis_pos                # 广播到 [B, N_vis, H]

        # 2) 合并视觉+文本，再加模态嵌入
        inputs_embeds = torch.cat([visual_embeds, txt_embeds], dim=1)  # [B, N_vis+N_txt, H]
        # modality_ids: 前 N_vis 全 0，后 N_txt 全 1
        modal_ids = torch.cat([
            torch.zeros(B, N_vis, dtype=torch.long, device=self.device),
            torch.ones(B,  N_txt, dtype=torch.long, device=self.device)
        ], dim=1)                                                  # [B, N_vis+N_txt]
        modal_embeds = self.modality_embed(modal_ids)             # [B, N_vis+N_txt, H]

        inputs_embeds = inputs_embeds + modal_embeds

        # ——— attention mask 同你原来那样拼接 ———
        attn_mask = torch.cat([
            torch.ones(B, N_vis, device=self.device),
            tokens.attention_mask
        ], dim=1)                                                  # [B, N_vis+N_txt]

        return inputs_embeds, attn_mask


        
    def forward(self, image, caption, cls_labels, clip_memory, criterion_cls, base_probs):
        """前向训练：分类 & 报告生成"""
        # 存储 base_probs 供 generate 使用
        self.base_probs = base_probs
        image = image.to(self.dtype).to(self.device)
        clip_memory = clip_memory.to(self.dtype).to(self.device)
        # 图像编码
        img_feats, vis_proj, avg_feats = self.encode_visual(image)
        # memory 融合
        avg_proj = self.vision_proj_mem(avg_feats)
        mem = torch.permute(clip_memory, (1,0,2))
        hs = self.memory(mem, None, avg_proj.unsqueeze(0), None).squeeze(0).squeeze(1)
        # 分类
        cls_input = torch.cat([avg_feats, hs], dim=1)
        cls_logits = self.cls_head(cls_input).view(-1,4,self.num_labels)
        # 添加先验
        probs = torch.tensor(base_probs, dtype=cls_logits.dtype, device=self.device)
        cls_logits[:,1,:] += torch.log(probs.view(1,-1))
        # 分类损失
        loss_cls = criterion_cls(cls_logits.permute(0,2,1), cls_labels)
        cls_preds = torch.argmax(cls_logits, dim=1).tolist()
        # 构建 prompt
        prompts = build_prompt_from_cls_preds_en(cls_preds)
        # 文本编码
        cap_tokens = self.tokenizer(caption, return_tensors='pt', padding=True, truncation=True, max_length=384).to(self.device)
        cap_embeds = self.embed_tokens(cap_tokens.input_ids)
        labels = cap_tokens.input_ids.masked_fill(cap_tokens.input_ids==self.tokenizer.pad_token_id, -100)
        # 拼接输入
        inputs_embeds, attn_mask = self.construct_inputs(vis_proj, prompts)
        full_inputs = torch.cat([inputs_embeds, cap_embeds], dim=1)
        full_mask = torch.cat([attn_mask, cap_tokens.attention_mask], dim=1)
        prefix_len = inputs_embeds.size(1)
        label_pref = torch.full((image.size(0), prefix_len), -100, device=self.device)
        full_labels = torch.cat([label_pref, labels], dim=1)
        # 语言模型损失
        outputs = self.llama(inputs_embeds=full_inputs, attention_mask=full_mask, labels=full_labels, return_dict=True)
        loss_lm = outputs.loss
        return loss_lm, loss_cls

    def generate(self, image, clip_memory, sample=False, num_beams=3, max_length=100, min_length=10, top_p=0.9, repetition_penalty=1.0, debug=False):
        """推理生成：返回报告, 分类预测, positive_probs"""
        with torch.no_grad():
            image = image.to(self.dtype).to(self.device)
            clip_memory = clip_memory.to(self.dtype).to(self.device)
            # 图像编码 & memory
            img_feats, vis_proj, avg_feats = self.encode_visual(image)
            avg_proj = self.vision_proj_mem(avg_feats)
            mem = torch.permute(clip_memory, (1,0,2))
            hs = self.memory(mem, None, avg_proj.unsqueeze(0), None).squeeze(0).squeeze(1)
            # 分类预测
            cls_input = torch.cat([avg_feats, hs], dim=1)
            cls_logits = self.cls_head(cls_input).view(-1,4,self.num_labels)
            # 先验
            if self.base_probs is not None:
                if not hasattr(self, 'base_probs_tensor'):
                    self.base_probs_tensor = torch.tensor(self.base_probs, dtype=cls_logits.dtype, device=self.device)
                cls_logits[:,1,:] += torch.log(self.base_probs_tensor.view(1,-1))
            cls_preds = torch.argmax(cls_logits, dim=1).tolist()
            # 构建 prompt
            prompts = build_prompt_from_cls_preds_en(cls_preds)
            # 构造模型输入
            inputs_embeds, attn_mask = self.construct_inputs(vis_proj, prompts)
            # 文本生成
            outputs = self.llama.generate(
                inputs_embeds=inputs_embeds,
                attention_mask=attn_mask,
                max_new_tokens=max_length,
                num_beams=num_beams,
                do_sample=sample,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                eos_token_id=self.tokenizer.eos_token_id,
                pad_token_id=self.tokenizer.pad_token_id
            )
            decoded = self.tokenizer.batch_decode(outputs, skip_special_tokens=True)
            # 后处理
            reports = []
            for text,prompt in zip(decoded, prompts):
                clean = text.split(prompt)[-1].strip()
                # 去重句子
                rpt = '. '.join(x for x,_ in groupby(clean.split('. ')))
                reports.append(rpt)
            # positive 概率
            positive_probs = torch.softmax(cls_logits, dim=1)[:,1,:]
            return reports, cls_preds, positive_probs
            

def blip_decoder(args):
    model = BLIP_Decoder(args)
    return model  

  
    