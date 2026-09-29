# Step 5：把向量存进 Qdrant，再查回来

[上一篇](step4.md) · [教程目录](README.md) · [下一篇](step6.md)

## 本篇目标与前置条件

完成第 4 篇的相似度演示。本篇学习数据库本身，用人为构造的 3 维向量即可，**不加载 BGE-M3**。

完成后你能解释：一个数据库点保存了什么，查询为什么能同时返回数字分数和原文。

## 1. 只记住三个概念

| 概念 | 可以怎样理解 |
| --- | --- |
| collection | 一组向量记录的集合 |
| vector | 用于比较相似度的一组数字 |
| payload | 跟向量一起保存的正文、来源、标题等数据 |

我们正式使用一个固定集合 `rag_tutorial`。本篇练习使用另一个集合 `tutorial_vectors`，其中是 3 维演示数据；第 6 篇正式集合保存 1024 维模型向量，两者分开。

## 2. 启动 Docker 中的 Qdrant

确保 Docker Desktop 已启动，并使用 Linux containers。没有 Docker 时先安装 Docker Desktop、按它的引导完成 WSL 2 设置并重启，然后继续。

在根目录新建 `infra` 目录，新建 `infra/compose.yaml`：

<!-- file: infra/compose.yaml -->
```yaml
name: rag-tutorial
services:
  qdrant:
    image: qdrant/qdrant:v1.14.1
    ports:
      - "127.0.0.1:6333:6333"
    volumes:
      - rag_qdrant_data:/qdrant/storage
volumes:
  rag_qdrant_data:
    name: rag_qdrant_data
```

终端在项目根目录运行：

```powershell
docker version
docker compose -f infra/compose.yaml config
docker compose -f infra/compose.yaml up -d
docker compose -f infra/compose.yaml ps
Invoke-RestMethod 'http://127.0.0.1:6333/collections'
```

`config` 检查配置，`up -d` 在后台启动容器，`ps` 查看状态。首次会拉取镜像。最后一条命令应返回集合列表；之前没有数据时列表为空，已有其他练习集合时不必清空。

这里把固定镜像版本直接写在文件里，不另外维护 Docker 专用环境文件。Windows 数据使用命名卷保存；停止容器不会自动清空这个卷。[Qdrant 官方本地入门](https://qdrant.tech/documentation/quickstart/)

## 3. 安装 Python 客户端并添加配置

把 `backend/requirements.txt` 更新为：

<!-- file: backend/requirements.txt -->
```text
pydantic==2.11.7
pydantic-settings==2.9.1
FlagEmbedding==1.3.5
transformers==4.51.3
sentence-transformers==4.1.0
peft==0.15.2
numpy==1.26.4
qdrant-client==1.14.3
```

执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

更新 `backend/app/config.py`，本次只新增数据库地址和集合名：

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
    qdrant_url: str = 'http://127.0.0.1:6333'
    qdrant_collection: str = 'rag_tutorial'

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
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=rag_tutorial
```

注意 `QDRANT_COLLECTION` 是集合名。如果你曾用旧教程，不要保留 `QDRANT_ALIAS` 或其他已移除字段。新的代码从来不读取 alias。

## 4. 写数据库访问层

新建 `backend/app/vector_store.py`：

<!-- file: backend/app/vector_store.py -->
```python
from qdrant_client import QdrantClient, models


class VectorStore:
    def __init__(self, settings, client=None):
        self.name = settings.qdrant_collection
        self.client = client if client is not None else QdrantClient(
            url=settings.qdrant_url, timeout=15,
        )

    def rebuild(self, dimension):
        if self.client.collection_exists(self.name):
            self.client.delete_collection(self.name)
        self.client.create_collection(
            collection_name=self.name,
            vectors_config=models.VectorParams(
                size=dimension, distance=models.Distance.COSINE,
            ),
        )

    def upsert(self, chunks, vectors):
        if len(chunks) != len(vectors):
            raise ValueError('片段数和向量数必须一致')
        points = [
            models.PointStruct(
                id=chunk.chunk_id, vector=vector, payload=chunk.payload(),
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        if points:
            self.client.upsert(
                collection_name=self.name, points=points, wait=True,
            )

    def query(self, vector, limit):
        return self.client.query_points(
            collection_name=self.name,
            query=vector, limit=limit,
            with_payload=True, with_vectors=False,
        ).points

    def count(self):
        return self.client.count(
            collection_name=self.name, exact=True,
        ).count

    def close(self):
        self.client.close()
```

按你会用到的顺序理解：

- `rebuild()`：删掉配置指定的集合，再建空集合。它只在正式导入或初始化演示集合时调用，不在每次查询时调用。
- `upsert()`：将片段和对应向量一起写入。`zip` 按位置配对；长度检查能避免悄悄漏掉尾部数据。
- `query()`：按向量查询，返回分数和 payload。`with_vectors=False` 表示不必把大串数字再传回来。
- `count()`：查看点的数量。
- `close()`：释放客户端连接。

`wait=True` 表示等待这次写入更新完成后再返回，方便紧接着查询；它不是本教程的事务或回滚系统。`client=None` 是一个小测试入口，第 12 篇使用本地内存客户端验证，无需启动 Docker。

## 5. 运行独立数据库练习

新建 `backend/scripts/check_qdrant.py`：

<!-- file: backend/scripts/check_qdrant.py -->
```python
from backend.app.config import load_settings
from backend.app.loader import make_document
from backend.app.splitter import split_document
from backend.app.vector_store import VectorStore


def main():
    settings = load_settings()
    settings.qdrant_collection = 'tutorial_vectors'
    store = VectorStore(settings)
    try:
        if not store.client.collection_exists(store.name):
            store.rebuild(3)
            docs = [
                make_document('example/a.txt', '甲', '', 'text', '向量甲'),
                make_document('example/b.txt', '乙', '', 'text', '向量乙'),
            ]
            chunks = [split_document(doc)[0] for doc in docs]
            store.upsert(chunks, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        rows = store.query([1.0, 0.0, 0.0], 1)
        print('集合：', store.name, '条数：', store.count())
        print('最接近的正文：', rows[0].payload['text'])
        assert store.count() == 2
        assert rows[0].payload['text'] == '向量甲'
    finally:
        store.close()


if __name__ == '__main__':
    main()
```

运行：

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.check_qdrant
```

应看到 `tutorial_vectors`、条数 2、最接近正文为“向量甲”。查询向量与甲完全相同、与乙垂直，所以这个例子易于验证；这些人工数字没有学习中文含义。

再验证一次持久化：

```powershell
docker compose -f infra/compose.yaml restart
```

等 `/collections` 可访问后，再运行同一个检查脚本。已有集合时脚本只查询，不重新写入；仍能查到，才说明重启后数据保留了。

## 为什么这么设计

先把数据库单独跑通，再接模型，能够把问题分清：这一篇失败就查 Docker、客户端和向量维度；下一篇才需要考虑模型输入。

固定集合让你专注于存取流程。更新时暂停查询并全量重建，已经足够支撑本机学习，不需要先掌握版本切换、清单文件或发布锁。

## 排查与验收

| 现象 | 处理 |
| --- | --- |
| Docker 只有 Client 没有 Server | 等 Docker Desktop 的引擎启动完成 |
| 6333 端口占用 | 查看是否已运行一份 Qdrant，不要重复启动冲突服务 |
| 连接被拒绝 | 检查容器状态和 `QDRANT_URL` |
| 向量维度错误 | 确认演示集合是 `tutorial_vectors`，不要混用正式集合 |

- [ ] 能说明 vector 和 payload 的区别。
- [ ] 3 维演示查询返回“向量甲”。
- [ ] 容器重启后仍能查到演示记录。
- [ ] 知道 `rebuild()` 会替换指定集合里的索引数据，原始资料文件不受影响。

---

完成验收后进入 [step6](step6.md)。
