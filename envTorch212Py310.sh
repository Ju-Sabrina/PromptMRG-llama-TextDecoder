# 1. 创建 Python 3.10 环境
conda create -n ju_py310_torch212 python=3.10 -y
conda activate ju_py310_torch212

# 2. 安装 PyTorch 2.1.2 + torchvision（CUDA 11.8）
pip install torch==2.1.2 torchvision==0.16.2 --index-url https://download.pytorch.org/whl/cu118

# 3. 安装 transformers + bitsandbytes + triton（支持 8bit/4bit）
pip install transformers==4.35.2 bitsandbytes==0.41.1 triton==2.1.0

# 4. 安装 PEFT（LoRA 支持）和 accelerate（Trainer 加速）
pip install peft==0.7.1 accelerate==0.27.2

# 5. 安装常用工具包
pip install opencv-python scipy pandas scikit-learn timm fairscale
