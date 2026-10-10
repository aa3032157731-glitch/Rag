# Step 7 参考答案

下面的代码已经在你的 `.venv` 里跑过：测试全部通过，也用真实的 BGE-M3 测过分数。用的是内存版 Qdrant，资料是那 3 份合成资料。

## backend/app/retriever.py

```python
from backend.app.models import Hit


class Retriever:
    def __init__(self, model, store, top_k=5, max_context_chars=4000, min_score=None):
        self.model = model
        self.store = store
        self.top_k = top_k
        self.max_context_chars = max_context_chars
        self.min_score = min_score

    def search(self, question, top_k=None):
        question = question.strip()
        if not question:
            raise ValueError('问题不能为空')
        rows = self.store.query(self.model.encode_query(question), top_k or self.top_k)
        hits = []
        used = 0
        for row in rows:
            if self.min_score is not None and row.score < self.min_score:
                break
            text = row.payload['text']
            if used + len(text) > self.max_context_chars:
                break
            used += len(text)
            hits.append(Hit(
                chunk_id=row.payload['chunk_id'],
                source=row.payload['source'],
                title=row.payload['title'],
                section=row.payload['section'],
                record_key=row.payload['record_key'],
                text=text,
                score=row.score,
            ))
        return hits
```

## backend/cli.py

```python
from backend.app.config import load_settings
from backend.app.embedding import EmbeddingService
from backend.app.retriever import Retriever
from backend.app.vector_store import VectorStore


def main():
    settings = load_settings()
    model = EmbeddingService(settings)
    store = VectorStore(settings)
    retriever = Retriever(model, store, settings.top_k, settings.max_context_chars, settings.min_score)
    try:
        while True:
            question = input('\n问题（直接回车退出）：').strip()
            if not question:
                break
            hits = retriever.search(question)
            if not hits:
                print('没有找到相关资料')
            for i, hit in enumerate(hits, 1):
                print(f'[{i}] {hit.score:.3f}  {hit.source}  {hit.section}')
                print('    ' + hit.text.replace('\n', ' '))
    finally:
        store.close()


if __name__ == '__main__':
    main()
```

几点说明：

- 有一种边界情况：如果单独一段就超过了 `max_context_chars`，会返回空列表。按 600 字切片、4000 字预算，这种情况不会出现；真出现了，说明配置本身不合理。
- 预算放在 Python 这边，Unity 就不用再管长度。放在 Unity 那边也行，只是要在两边都想清楚。

**为什么不截断片段？** 截成"每天 8:00 开门，22:"这样的半句话，会让大模型以为资料就写了这么多，甚至据此编出一个答案。少给一段，模型最多是"不知道"；给一段残缺的，模型可能会"答错"。

---

## 实验结果（实测）

### 实验 1 和实验 3：第 1 条结果的分数

| 问题 | 第 1 条 | 分数 |
|---|---|---|
| 补办校园卡要多少钱 | 校园卡补办 ✓ | 0.787 |
| 校园卡丢了怎么办 | 校园卡补办 ✓ | 0.679 |
| 图书馆几点关门 | 图书馆·开放时间 ✓ | 0.655 |
| 网络坏了找谁 | 网络报修 ✓ | 0.578 |
| 有素食吗 | 食堂 ✓ | 0.537 |
| 食堂晚餐几点（没有答案） | 食堂 | 0.682 |
| 校医院电话是多少（没有答案） | 网络报修 | 0.560 |
| 宿舍几点熄灯（没有答案） | 网络报修 | 0.526 |
| 今天天气怎么样（无关） | 图书馆 | 0.412 |
| 讲个笑话（无关） | 校园卡补办 | 0.342 |

1. 有答案的问题，第 1 条全部找对了。
2. 有答案的问题：0.54 到 0.79；跟校园有关但没答案的：0.53 到 0.68；完全无关的：0.34 到 0.41。
3. **找不到这样的分数线。** "食堂晚餐几点"（0.682）比"有素食吗"（0.537）分数还高，可前者资料里根本没有答案。

`MIN_SCORE=0.45` 能过滤掉**闲聊类**问题。这一点对数字人很重要：用户说"讲个笑话"时，不应该把校园资料塞给模型。

但是**跟校园有关、资料里又没有**的问题过滤不掉，而且换成任何阈值都解决不了：它们和有答案的问题在分数上是混在一起的。这类问题只能交给大模型，靠提示词告诉它"资料没写就说没有"。step9 的提示词就是这么写的。

> 0.45 这个值只适用于这份资料和这个模型。换了真实资料，要在 step10 用评估脚本重新确定。

### 实验 2：TOP_K

实测 `TOP_K=5` 时，第 2 到第 5 条的分数在 0.29 到 0.54 之间，内容和问题基本无关。只有 5 条数据的库，返回 5 条就等于"全文注入"。改成 3 之后，模型能少看一些噪音。

还有一个现象可以留意：正确结果和第 2 名之间通常差 0.1 以上（比如 0.655 对 0.518）。所以"只保留和第 1 名相差不到 0.1 的结果"也是一种过滤思路，可以在 step10 里试试效果，现在先不做。

### 实验 4：追问

"那周末呢"实测检索到的是**图书馆**（0.496）。如果上一句问的是食堂，这个结果就错了，而且错得悄无声息：检索照样返回了结果，分数看起来也正常。step9 会把上一句问题拼进检索词来解决这个问题；拼上之后，实测食堂排第 1（0.672）。
