# Step 7：输入问题，从知识库找到原文

[上一篇](step6.md) · [教程目录](README.md) · [下一篇](step8.md)

## 本篇目标与前置条件

已经用第 6 篇成功导入三份教学资料，Qdrant 正在运行。本篇把问题变成向量，再从正式集合查出最相近的原文。

完成后你会拥有一个能不断提问的命令行检索器。**现在返回的是原文，不是聊天模型生成的答案。**

## 1. 先理解查询流程

```text
问题 → 去掉首尾空白 → BGE-M3 编码
    → Qdrant 取 top_k 个相近片段
    → 可选分数过滤、上下文预算
    → 原文 + 来源 + 分数
```

`top_k` 是最多取多少个候选，不是“保证找到多少条正确答案”。本篇直接取 top_k，不添加候选扩增、近似去重或重排序。

上下文预算控制最终返回的正文总字符数，方便第 11 篇把资料交给聊天模型。某块放不下就整块跳过，避免切掉一句话的一半。

## 2. 增加现在才用到的配置

更新 `backend/app/config.py`；本次新增 `top_k`、`max_context_chars`、`min_score`：

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
    top_k: int = Field(default=5, ge=1, le=10)
    max_context_chars: int = Field(default=4000, ge=1)
    min_score: float | None = Field(default=None, ge=-1, le=1)

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
TOP_K=5
MAX_CONTEXT_CHARS=4000
# MIN_SCORE 暂不设置，不要添加空值行
```

`MIN_SCORE` 默认不设置，代码里的值是 `None`。不要写成空白的 `MIN_SCORE=`；如果以后需要实验阈值，再明确写一个数字，例如 `MIN_SCORE=0.5`，实验后删除该行即可恢复不筛选。

这不是推荐你现在固定为 0.5。阈值需要用资料和实际问题观察，不能拿相似度分数当正确概率。

## 3. 定义检索结果 Hit

更新 `backend/app/models.py`，在已有 Document、Chunk 后加入 Hit。下面是完整对照：

<!-- file: backend/app/models.py -->
```python
from dataclasses import dataclass, asdict
from hashlib import sha256
from uuid import uuid5, NAMESPACE_URL


def digest(text: str) -> str:
    return sha256(text.encode('utf-8')).hexdigest()


def stable_id(text: str) -> str:
    return str(uuid5(NAMESPACE_URL, 'rag-tutorial/' + text))


@dataclass(frozen=True)
class Document:
    document_id: str
    source: str
    title: str
    section: str
    record_key: str
    text: str
    content_hash: str

@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    source: str
    title: str
    section: str
    record_key: str
    chunk_index: int
    text: str
    content_hash: str

    def payload(self):
        return asdict(self)

@dataclass(frozen=True)
class Hit:
    chunk_id: str
    source: str
    title: str
    section: str
    record_key: str
    text: str
    score: float
```

Document 对应文档单元，Chunk 对应存入数据库的片段，Hit 对应本次问题命中的片段。Hit 多出来的 `score` 是“这个问题与这段原文”的相似度分数。

## 4. 编写唯一一份检索逻辑

新建 `backend/app/retriever.py`：

<!-- file: backend/app/retriever.py -->
```python
from dataclasses import asdict
from backend.app.models import Hit


class Retriever:
    def __init__(self, settings, model, store):
        self.settings = settings
        self.model = model
        self.store = store

    def retrieve(self, query, top_k=None):
        query = query.strip()
        if not query or len(query) > 2000:
            raise ValueError('问题需要 1～2000 个字符')
        top_k = self.settings.top_k if top_k is None else top_k
        if not 1 <= top_k <= 10:
            raise ValueError('top_k 需要在 1～10 之间')
        vector = self.model.encode_query(query)
        rows = self.store.query(vector, top_k)
        hits = []
        used = 0
        for row in rows:
            if self.settings.min_score is not None and row.score < self.settings.min_score:
                continue
            payload = row.payload
            if used + len(payload['text']) > self.settings.max_context_chars:
                continue
            hits.append(Hit(
                chunk_id=str(row.id),
                source=payload['source'],
                title=payload['title'],
                section=payload['section'],
                record_key=payload['record_key'],
                text=payload['text'],
                score=float(row.score),
            ))
            used += len(payload['text'])
        return {
            'status': 'ok' if hits else 'no_match',
            'query': query,
            'results': [asdict(hit) for hit in hits],
        }
```

这里直接读取我们自己导入时写入的 payload 字段，没有每层再验证一次字段体系。如果你手动改了数据库，导致正文或来源字段缺失，应修复资料并重新导入，而不是让程序用空文字继续回答。

`status` 只有两种成功结果：

- `ok`：有片段通过筛选和预算。
- `no_match`：查询成功，但最终片段列表为空。

数据库连不上或模型出错会抛出异常。它们不是“知识库没有答案”，不能改成空列表掩盖错误。

## 5. 写交互式命令行入口

新建 `backend/cli.py`：

<!-- file: backend/cli.py -->
```python
import json
from backend.app.config import load_settings
from backend.app.embedding import EmbeddingService
from backend.app.retriever import Retriever
from backend.app.vector_store import VectorStore


def main():
    settings = load_settings()
    print('正在加载向量模型……')
    model = EmbeddingService(settings)
    store = VectorStore(settings)
    retriever = Retriever(settings, model, store)
    try:
        print('知识库片段数：', store.count())
        while True:
            try:
                question = input('问题（exit 退出）：')
            except (EOFError, KeyboardInterrupt):
                break
            if question.strip().lower() == 'exit':
                break
            try:
                result = retriever.retrieve(question)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except ValueError as exc:
                print('请调整问题：', exc)
    finally:
        store.close()


if __name__ == '__main__':
    main()
```

运行：

```powershell
.\.venv\Scripts\python.exe -m backend.cli
```

模型在 `while` 之外创建，所以同一个进程里的后续问题复用模型。输入问题后按 Enter，输入 `exit` 退出。空问题或超长问题会提示调整；数据库错误会保留原始报错，便于定位。

## 6. 马上用三个问题验收真实检索

先不要调参数，依次输入：

| 问题 | 返回原文里应找到的信息 |
| --- | --- |
| 图书馆几点关门？ | 开放时间章节中的 22:00 |
| 校园卡补办需要带什么？ | 身份证和学生证 |
| 第一食堂在哪里？ | 生活区南侧 |

展开输出中的 `results`，关注 `text`、`source`、`record_key`、`score`。JSON 中正文换行可能显示为 `\n`，这是序列化格式，不是内容损坏。

然后问“晚上九点还能进图书馆吗？”：应能查到开放时间原文。检索器不会替你组织“可以，十点关门”的完整回答；那是第 11 篇的工作。

## 7. 观察无关问题和预算的影响

问“明天会下雨吗？”。未设分数阈值时，数据库仍可能返回几个相对最近的校园片段。这不表示这些片段能回答天气。

再做一个可选小实验：

1. 输入 exit 退出，临时降低 `MAX_CONTEXT_CHARS`。
2. 重启 CLI，问相同问题，观察某些整段因预算不足而被跳过。
3. 恢复 `MAX_CONTEXT_CHARS=4000`，重启后继续。

配置是在进程启动时读取的，改文件后不会自动刷新。本篇只有检索参数变化，不改变文档向量，因此无需为 top_k 或分数阈值变化重新导入。

## 为什么这么设计

Retriever 负责“问题如何找到资料”，CLI 只负责输入和打印。下一篇 HTTP 接口调用同一个 Retriever，就不会产生两套检索算法。

先用三个已知问题核对原文，能在接界面和聊天模型之前发现资料、切分或导入问题。保留来源和完整原文，也让你能够追查后面生成的答案有没有依据。

## 排查与验收

| 现象 | 处理 |
| --- | --- |
| 集合不存在 | 先完成第 6 篇导入，核对 QDRANT_COLLECTION |
| 查到旧内容 | 修改原文后重新导入 |
| 无关问题也有结果 | 近邻查询的正常限制；判断相关性不能只看“有返回” |
| 结果为空 | 检查阈值和正文预算，确认集合里有数据 |
| 每次问都加载模型 | 模型创建是否误放进了循环 |

- [ ] 三个已知问题都能找到所需原文。
- [ ] 能区分检索结果、生成答案和程序故障。
- [ ] 能说明 top_k 与实际结果条数为什么可能不同。
- [ ] 输入 exit 退出，再进入下一篇，避免多份模型同时占内存。

---

下一篇：[step8：把检索变成 HTTP 接口](step8.md)。
