# Step 4：运行向量模型，第一次看见语义相似度

[上一篇](step3.md) · [教程目录](README.md) · [下一篇](step5.md)

## 本篇目标与前置条件

完成第 3 篇。首次需要联网下载模型，之后编码在本机进行。先退出其他会加载模型的 Python 进程，保持这一篇只有一个模型实例。

本篇成果：给出一个问题，将三条短文本按相似度排序。这是一个很小的检索演示；还没有将数据存入数据库，也没有调用聊天模型。

## 1. 先分清两个模型

| 模型 | 输入和输出 | 本项目中的位置 |
| --- | --- | --- |
| BGE-M3 向量模型 | 一段文字 → 一组数字 | 第 4 篇，用于找资料 |
| DeepSeek 聊天模型 | 问题和资料 → 回答 | 第 11 篇，用于组织答案 |

向量可以先理解为文本的数字表示。本教程使用 BGE-M3 的 dense 向量，维度为 1024。维度代表数字个数，不代表资料条数。模型也支持其他检索形式，本教程暂不展开。[模型官方说明](https://huggingface.co/BAAI/bge-m3)

## 2. 安装本篇依赖

将 `backend/requirements.txt` 更新为以下完整内容：

<!-- file: backend/requirements.txt -->
```text
pydantic==2.11.7
pydantic-settings==2.9.1
FlagEmbedding==1.3.5
transformers==4.51.3
sentence-transformers==4.1.0
peft==0.15.2
numpy==1.26.4
```

终端在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

这套主线使用 CPU，最后打印 `False` 是正常的，不代表显卡损坏。已有满足条件的 CPU 环境可直接核对；如果你已配置 GPU 环境，保留备份和安装记录后再决定是否切换，不要反复混装。

`torch` 单独安装是因为 CPU wheel 使用单独的安装源，其余依赖仍用上一篇的清单方式管理。[PyTorch 固定版本安装说明](https://pytorch.org/get-started/previous-versions/)

## 3. 到这里再增加模型配置

更新 `backend/app/config.py`。新增的是模型名、版本、缓存、batch、token 上限，以及创建缓存的方法：

<!-- file: backend/app/config.py -->
```python
from pathlib import Path
import json
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / 'backend' / '.env',
        env_file_encoding='utf-8',
    )
    documents_dir: Path = Path('data/documents')
    chunk_size: int = 600
    chunk_overlap: int = 100
    model_name: str = 'BAAI/bge-m3'
    model_revision: str = '5617a9f61b028005a4858fdac845db406aefb181'
    model_cache: Path = Path('models/huggingface')
    batch_size: int = Field(default=1, ge=1)
    max_tokens: int = Field(default=1024, ge=32, le=8192)

    def prepare_cache(self):
        self.model_cache.mkdir(parents=True, exist_ok=True)
        os.environ['HF_HOME'] = str(self.model_cache)


def load_settings():
    settings = Settings()
    settings.documents_dir = (ROOT / settings.documents_dir).resolve()
    settings.model_cache = (ROOT / settings.model_cache).resolve()
    return settings


if __name__ == '__main__':
    settings = load_settings()
    print(json.dumps(settings.model_dump(mode='json'), ensure_ascii=False, indent=2))
```

更新 `backend/.env.example` 和 `backend/.env`：

<!-- file: backend/.env.example -->
```dotenv
DOCUMENTS_DIR=data/documents
CHUNK_SIZE=600
CHUNK_OVERLAP=100
MODEL_NAME=BAAI/bge-m3
MODEL_REVISION=5617a9f61b028005a4858fdac845db406aefb181
MODEL_CACHE=models/huggingface
BATCH_SIZE=1
MAX_TOKENS=1024
```

| 参数 | 当前值的含义 |
| --- | --- |
| `MODEL_REVISION` | 固定模型仓库的一个版本，帮助复现 |
| `MODEL_CACHE` | 模型文件缓存在项目哪里 |
| `BATCH_SIZE=1` | 模型一次处理一条文本，先降低内存压力 |
| `MAX_TOKENS=1024` | 本项目选择的单段输入上限 |

模型名、模型版本和输入处理方式改变后，需要重新生成文档向量；我们用操作步骤保证一致性，不写索引指纹系统。

## 4. 编写 EmbeddingService

新建 `backend/app/embedding.py`：

<!-- file: backend/app/embedding.py -->
```python
class EmbeddingService:
    dimension = 1024

    def __init__(self, settings):
        self.settings = settings
        settings.prepare_cache()
        from huggingface_hub import snapshot_download
        from FlagEmbedding import BGEM3FlagModel

        local_path = snapshot_download(
            repo_id=settings.model_name,
            revision=settings.model_revision,
            cache_dir=str(settings.model_cache / 'hub'),
            allow_patterns=[
                '*.json', '*.model', 'tokenizer*', 'sentencepiece*',
                'pytorch_model.bin', 'model.safetensors',
                'colbert_linear.pt', 'sparse_linear.pt',
            ],
        )
        self.model = BGEM3FlagModel(
            local_path,
            devices='cpu',
            use_fp16=False,
            normalize_embeddings=True,
        )
        self.tokenizer = self.model.tokenizer

    def count_tokens(self, text):
        return len(self.tokenizer.encode(
            text, add_special_tokens=True, truncation=False,
        ))

    def encode_documents(self, texts):
        if not texts:
            return []
        for text in texts:
            if not text.strip():
                raise ValueError('不能编码空文字')
            if self.count_tokens(text) > self.settings.max_tokens:
                raise ValueError('文字超过 token 上限：文档先分段，问题请缩短')
        vectors = self.model.encode(
            texts,
            batch_size=self.settings.batch_size,
            max_length=self.settings.max_tokens,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )['dense_vecs']
        if vectors.shape != (len(texts), self.dimension):
            raise RuntimeError(f'向量形状与预期不符：{vectors.shape}')
        return vectors.tolist()

    def encode_query(self, query):
        return self.encode_documents([query])[0]
```

阅读时分成三部分：

1. 构造函数准备缓存、下载并加载模型。`snapshot_download` 下载固定版本需要的文件，`local_path` 是本机目录。
2. `count_tokens()` 用模型自己的分词器计数；`truncation=False` 是为了看见真实长度。
3. `encode_documents()` 调用模型得到 dense 向量，`encode_query()` 用同一套编码方式处理问题。

`normalize_embeddings=True` 让向量长度归一化。后面的小演示用点积比较相似度，因此不用再手写一遍归一化。

输入超长时直接提醒，不悄悄截掉文档尾部。第 6 篇会在导入前继续细分这些文本；问题过长则由提问者缩短。

## 5. 写一个短小的观察脚本

新建 `backend/scripts/check_embedding.py`：

<!-- file: backend/scripts/check_embedding.py -->
```python
from backend.app.config import load_settings
from backend.app.embedding import EmbeddingService


def main():
    model = EmbeddingService(load_settings())
    texts = [
        '图书馆每天八点开门，晚上十点关门。',
        '第一食堂二楼提供素食。',
        '校园卡补办需要身份证和学生证。',
    ]
    question = '晚上九点还可以进图书馆吗？'
    vectors = model.encode_documents(texts)
    query_vector = model.encode_query(question)
    print('向量条数：', len(vectors), '每条维度：', len(vectors[0]))
    print('问题 token 数：', model.count_tokens(question))
    scores = [
        sum(a * b for a, b in zip(query_vector, vector))
        for vector in vectors
    ]
    for score, text in sorted(zip(scores, texts), reverse=True):
        print(f'{score:.4f} | {text}')


if __name__ == '__main__':
    main()
```

运行：

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.check_embedding
```

第一次下载和加载可能较慢。模型文件较大，先确认磁盘空间和网络可用，看到下载进度时耐心等待；不要同时再开多个进程重复加载。

你应看到：3 条向量、每条 1024 个数字，以及三行按分数排序的原文。图书馆句子通常应最靠前；具体分数以本机实测为准，教程不预填虚假的模型运行结果。

如果运行成功，试着将问题换为“补办校园卡需要什么材料？”，重新运行并观察排序。每次运行脚本会重新加载模型，这是单次演示；后面的 CLI 和 API 会在进程中复用模型。

## 为什么这么设计

先用三句话看见排序，再学习数据库，你就能明白数据库保存和比较的是什么。`EmbeddingService` 隔离了模型调用，后面的导入和提问共用它，避免两边编码方式不同。

这一篇不要求记录 30 次延迟、p95 或调 GPU。首先确认文本能够变成正确维度的向量，且相关文本有较合理的排序，再继续构建完整检索。

## 排查与验收

| 现象 | 处理 |
| --- | --- |
| 模型下载失败 | 检查网络、模型仓库可访问性和磁盘空间；再次运行会利用已有缓存 |
| 很慢 | 分清下载、加载、编码；CPU 基线允许较慢 |
| 依赖导入报错 | 核对固定版本，运行 pip check，保留具体错误 |
| token 超限 | 使用短问题；正式文档导入到第 6 篇处理 |

- [ ] 输出 3 条 1024 维向量。
- [ ] 改变问题能观察到排序变化。
- [ ] 明白相似度不是“答案正确的概率”。
- [ ] 明白本篇仍没有生成自然语言回答。

---

完成验收后进入 [step5](step5.md)。
