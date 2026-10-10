# Step 6 参考答案

对照时不需要逐行一样，重点看三件事：

1. `ingest` 里先编码、后 `recreate`；
2. `recreate` 是先删、再建；
3. 测试全部通过。

下面的代码已经在你的 `.venv` 里实际跑过，16 个测试全部通过；也用真实的 BGE-M3 跑过一遍导入流程（用的是内存版 Qdrant，没有动你 Docker 里的数据库）。

---

## backend/app/vector_store.py

```python
from qdrant_client import QdrantClient, models


class VectorStore:
    def __init__(self, settings, client=None, name=None):
        self.name = name or settings.qdrant_collection
        self.client = client or QdrantClient(url=settings.qdrant_url, timeout=15)

    def recreate(self, dimension):
        if self.client.collection_exists(self.name):
            self.client.delete_collection(self.name)
        self.client.create_collection(
            collection_name=self.name,
            vectors_config=models.VectorParams(
                size=dimension,
                distance=models.Distance.COSINE,
            ),
        )

    def upsert(self, chunks, vectors):
        if len(chunks) != len(vectors):
            raise ValueError('片段数与向量数不同')
        points = [
            models.PointStruct(id=chunk.chunk_id, vector=vector, payload=chunk.payload())
            for chunk, vector in zip(chunks, vectors)
        ]
        self.client.upsert(collection_name=self.name, points=points, wait=True)

    def query(self, vector, limit):
        return self.client.query_points(
            collection_name=self.name,
            query=vector,
            limit=limit,
            with_payload=True,
        ).points

    def count(self):
        return self.client.count(collection_name=self.name, exact=True).count

    def close(self):
        self.client.close()
```

## backend/scripts/check_qdrant.py

```python
import argparse
from uuid import uuid4

from qdrant_client import models

from backend.app.config import load_settings
from backend.app.vector_store import VectorStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', help='重启后查询上次创建的测试 collection')
    args = parser.parse_args()
    name = args.name or 'tutorial_' + uuid4().hex[:12]
    store = VectorStore(load_settings(), name=name)
    try:
        if not args.name:
            store.recreate(3)
            store.client.upsert(store.name, points=[
                models.PointStruct(id=1, vector=[1.0, 0.0, 0.0], payload={'text': '甲'}),
                models.PointStruct(id=2, vector=[0.0, 1.0, 0.0], payload={'text': '乙'}),
            ], wait=True)
        rows = store.query([1.0, 0.0, 0.0], 1)
        assert rows and rows[0].payload['text'] == '甲'
        print('验证通过, collection', store.name)
        print('重启后运行: python -m backend.scripts.check_qdrant --name', store.name)
    finally:
        store.close()


if __name__ == '__main__':
    main()
```

## backend/ingest.py

```python
from backend.app.config import load_settings
from backend.app.embedding import EmbeddingService
from backend.app.loader import load_documents
from backend.app.splitter import split_document, fit_token_budget, embedding_text
from backend.app.vector_store import VectorStore


def load_chunks(settings):
    report = load_documents(settings.documents_dir)
    if report.errors:
        raise ValueError('资料有错误：\n' + '\n'.join(report.errors))
    chunks = [
        chunk
        for doc in report.documents
        for chunk in split_document(doc, settings.chunk_size, settings.chunk_overlap)
    ]
    if not chunks:
        raise ValueError(f'{settings.documents_dir} 里没有可导入的资料')
    return chunks


def ingest(chunks, model, store, max_tokens):
    chunks = fit_token_budget(chunks, model.count_tokens, max_tokens)
    vectors = model.encode_documents([embedding_text(c) for c in chunks])
    store.recreate(model.dimension)
    store.upsert(chunks, vectors)
    return store.count()


def main():
    settings = load_settings()
    chunks = load_chunks(settings)
    print('字符分段：', len(chunks))
    model = EmbeddingService(settings)
    store = VectorStore(settings)
    try:
        count = ingest(chunks, model, store, settings.max_tokens)
        print(f'导入完成：{store.name} 共 {count} 条')
    finally:
        store.close()


if __name__ == '__main__':
    main()
```

几点说明：

- **import 写在文件顶部就可以。** 你的 `embedding.py` 只在 `EmbeddingService.__init__` 里面才 import torch，所以 import 这个模块本身很快。我实际检查过：import `backend.ingest` 之后 torch 并没有被加载。
- **`ingest` 的参数是 `chunks`，不是 `settings`。** 这样它只负责"编码并写入"这一件事，不碰文件系统。
- **没有手动分批。** `encode_documents` 已经把 `batch_size` 传给了模型，模型内部会自己分批。所有向量一次性放在内存里，对几份资料没有问题；等资料到了上千份，才需要改成边编码边写入。

---

## 思考题

**1. `upsert` 为什么要检查长度？**

`zip` 会按较短的那一边停下来。传 3 个 chunk 和 2 个向量，第 3 个 chunk 会被**悄悄丢掉**：不报错，条数显示 2，你不会知道少了东西。直到有一天问到那段内容、怎么也检索不到时才会发现。这种检查才是值得写的防御代码：它防的是一个真实会发生、而且发生了也看不出来的错误。

**2. payload 为什么要存整个 chunk？**

Qdrant 检索返回的是"点"。如果 payload 里只有 id，还得再去别处查原文。存了 `text`，才能把原文交给 DeepSeek；存了 `source`、`title`、`section`，数字人才能告诉用户"根据《图书馆》的开放时间部分……"；`record_key` 和 `chunk_index` 用来排查问题时定位到原文的具体位置。

---

## 实验

### 实验 1：不重建会怎样

我实际跑过一遍，结果是 **5 → 5 → 6**。

- **运行两次还是 5 条。** `chunk_id` 是由文档 id、内容 hash、序号和片段文字算出来的。内容不变，id 就不变；`upsert` 碰到同一个 id 会覆盖原来的点，不会新增。
- **改成 21:00 之后变成 6 条。** "开放时间"这个文档单元的内容变了，`content_hash` 跟着变，`chunk_id` 也变了，于是写进去一个新的点；而原来 22:00 的那个旧点没有人去删。
- **会检索到什么？** "22:00 关门"和"21:00 关门"两段都会被找到，分数几乎一样。数字人可能随便挑一个回答，也可能说资料自相矛盾。删除文件也是同样的问题：被删文件的片段会永远留在库里。

`recreate` 这一行代码，把"新增、修改、删除"三种情况统一处理掉了。代价是每次都要全量编码。等资料多到全量编码太慢时，才需要做增量更新（按 `content_hash` 判断哪些内容变了），那是很后面的事。

### 实验 2：MAX_TOKENS=32

实测变成了 **14 条**。其中有两段是这样的：

```text
campus/library.md | 开放时间 | '星河校园图书馆周一至周日每天 8'
campus/library.md | 开放时间 | ':00 开门，22:00 关门。'
...
campus/services.json |  | 'content : 校园卡补办在服务'
campus/services.json |  | '楼一楼办理，需携带身份证和学生证。\n'
```

`8:00` 被从中间切断了，"服务楼"也被拆开了。原因有两个：

1. `embedding_text` 里除了正文还有标题和章节。"星河校园图书馆（合成测试资料）"本身就占了十几个 token，留给正文的预算更少了。
2. 你的 `boundary_position` 只认 `\n。！？；.!?;` 这些分隔符，**不认逗号**。一句话中间没有句号时，就只能从正中间硬切。

问"图书馆几点关门"时，`':00 开门，22:00 关门。'` 这一段因为带着标题和章节，大概率还能被检索到；但数字人拿到的原文是残缺的。

结论：片段太大，一段里混着好几个话题，向量的意思会被"平均"掉；片段太小，语义就被切断了。按 600 字、1024 token 来切，这几份资料都能保持完整。这是 RAG 里最常调整的参数，step10 做对比时可以回来试几组。

可选的改进（不做也行）：给 `boundary_position` 加一档逗号。先找句末标点，找不到再退一步找 `，,`，最后才从中间切。

### 实验 3：Qdrant 没开

- **错误出现在哪里？** 在 `recreate` 第一次访问 Qdrant（`collection_exists`）的时候，也就是模型加载（约 15 秒）和编码全部做完之后。前面这些时间都白等了。`QdrantClient(url=...)` 在创建对象时不会因为连不上而报错，这一点我也实际确认过。
- **为什么报的是 502？** 我在你这台机器的终端里实测，报的是 `UnexpectedResponse: 502 (Bad Gateway)`，而不是"连接被拒绝"。原因是 Windows 系统代理接管了发往 127.0.0.1 的请求：Qdrant 不在的时候，是代理返回了 502。以后看到 502，别以为是 Qdrant 内部出错了，先检查容器有没有在运行。
- **怎样更早发现？** 在 `main` 里加载模型之前，先访问一次 Qdrant（比如 `store.client.get_collections()`），连不上就立刻退出。这和规则 1 是同一个思路：便宜的检查放前面，昂贵的操作放后面。

---

## 和旧教程相比删掉了什么，以后什么时候加回来

| 旧代码 | 现在 | 什么时候需要加回来 |
|---|---|---|
| alias、`switch_alias`、manifest | `recreate` | 数字人长期在线、导入时不能停止服务 |
| `import_lock` | 没有 | 定时任务导入，或者多人同时导入 |
| 用 `ServiceUnavailable` 包装异常 | 直接抛出 Qdrant 的异常 | step8 做 HTTP 接口时，要把"数据库挂了"翻译成 503 |
| 分批编码，并打印进度 | 一次编码全部 | 资料到了上千份 |
| `--dry-run` | 由 `load_chunks` 先单独检查资料 | — |
