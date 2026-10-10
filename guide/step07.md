# Step 7：在命令行提问，找到原文

**做完这一步**：运行 `python -m backend.cli`，输入问题，就能看到最相关的原文片段和它们的分数。

**前提**：step6 已完成，`rag_tutorial` 里有 5 条数据，Qdrant 正在运行。

这一步还没有大模型参与。检索只负责"找资料"，回答是 step9 的事。先把这两件事分开看清楚。

---

## 0. 先改一个配置：BATCH_SIZE

照现在的配置，每问一次都会打印两行进度条（`pre tokenize`、`Inference Embeddings`）。原因在 FlagEmbedding 的源码里，你可以自己去看：

```
.venv\Lib\site-packages\FlagEmbedding\inference\embedder\encoder_only\m3.py 第 414 行
```

```python
for start_index in tqdm(..., desc="Inference Embeddings",
                        disable=len(sentences) < batch_size):
```

只有"句子数 < batch_size"时，进度条才会关掉。你的 `BATCH_SIZE=1`，查询只有 1 句，`1 < 1` 不成立，所以每次都会打印。

把 `backend/.env` 里的 `BATCH_SIZE` 改成 `8`。这样查询时就不再打印进度条，导入时也会一次编码 8 段，速度更快。几百字的片段在 CPU 上一批 8 段，内存完全够用。

> 遇到库的行为看不懂时，直接去读它的源码。这比搜索"怎么关掉进度条"更快，也更可靠。

---

## 1. 检索到底做了什么

```text
问题 "图书馆几点关门"
  → model.encode_query()   得到 1024 个数字（问题向量）
  → store.query(向量, k)    Qdrant 找出余弦相似度最高的 k 个点
  → 每个点的 payload        原文、来源、标题、章节
```

**分数**是问题向量和片段向量的余弦相似度。两个向量都已经归一化，所以它就等于点积，理论范围是 -1 到 1。在这几份资料上，实际分数大多落在 0.3 到 0.8 之间。分数只能用来比较哪个更相关，**它不是"答对的概率"**。

---

## 2. 练习一：写 Retriever

新建 `backend/app/retriever.py`：

```python
class Retriever:
    def __init__(self, model, store, top_k=5, max_context_chars=4000, min_score=None): ...
    def search(self, question, top_k=None):
        """返回 list[Hit]，按分数从高到低排列。"""
```

**规则**

- 先去掉问题两端的空白；去掉后是空字符串，就抛出 `ValueError`。
- 传了 `top_k` 就用传入的值，没传就用 `self.top_k`。step8 的接口会用到这个参数。
- 每个结果点都要转成 `Hit`。`Hit` 在 `models.py` 里已经定义好了，字段从 `row.payload` 取，分数从 `row.score` 取。
- 设置了 `min_score` 时，分数低于它的结果要丢掉。结果本来就按分数从高到低排好了，所以碰到第一个低于阈值的就可以停下。
- 所有返回片段的 `text` 总长度不能超过 `max_context_chars`。**不要截断片段**：如果加上某一段会超出预算，就在它前面停下。

**写之前先想一想**：为什么超出预算时宁可少返回一段，也不把最后一段截短？（提示：想象片段被截成了"每天 8:00 开门，22:"。）

### 用测试检查

新建 `backend/tests/test_retriever.py`：

```python
import unittest
from types import SimpleNamespace

from qdrant_client import QdrantClient

from backend.app.loader import make_document
from backend.app.retriever import Retriever
from backend.app.splitter import split_document
from backend.app.vector_store import VectorStore


class FakeModel:
    """假模型：问题里出现哪个关键词，就返回对应的方向向量。"""
    directions = {'图书馆': [1.0, 0.0, 0.0], '食堂': [0.0, 1.0, 0.0]}

    def encode_query(self, question):
        for word, vector in self.directions.items():
            if word in question:
                return vector
        return [0.0, 0.0, 1.0]


class RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.store = VectorStore(SimpleNamespace(qdrant_collection='test_retriever'),
                                 client=QdrantClient(':memory:'))
        self.store.recreate(3)
        texts = ['图书馆八点开门。', '图书馆在明德楼东侧。', '食堂二楼有素食。']
        vectors = [[1.0, 0.0, 0.0], [0.8, 0.6, 0.0], [0.0, 1.0, 0.0]]
        chunks = [split_document(make_document('t.txt', '测试', '', str(i), t))[0]
                  for i, t in enumerate(texts)]
        self.store.upsert(chunks, vectors)

    def tearDown(self):
        self.store.close()

    def test_results_sorted_by_score(self):
        hits = Retriever(FakeModel(), self.store).search('图书馆几点开门')
        self.assertEqual(hits[0].text, '图书馆八点开门。')
        self.assertEqual([h.score for h in hits], sorted([h.score for h in hits], reverse=True))

    def test_top_k_limits_count(self):
        hits = Retriever(FakeModel(), self.store, top_k=2).search('图书馆')
        self.assertEqual(len(hits), 2)
        self.assertEqual(len(Retriever(FakeModel(), self.store).search('图书馆', top_k=1)), 1)

    def test_min_score_drops_weak_hits(self):
        hits = Retriever(FakeModel(), self.store, min_score=0.5).search('图书馆')
        self.assertEqual([h.text for h in hits], ['图书馆八点开门。', '图书馆在明德楼东侧。'])
        self.assertEqual(Retriever(FakeModel(), self.store, min_score=0.5).search('天气'), [])

    def test_budget_keeps_whole_chunks(self):
        hits = Retriever(FakeModel(), self.store, max_context_chars=12).search('图书馆')
        self.assertEqual([h.text for h in hits], ['图书馆八点开门。'])

    def test_empty_question_rejected(self):
        with self.assertRaises(ValueError):
            Retriever(FakeModel(), self.store).search('   ')


if __name__ == '__main__':
    unittest.main()
```

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_retriever -v
```

---

## 3. 练习二：写命令行入口

新建 `backend/cli.py`，只写一个 `main()`：

- 读配置，创建 `EmbeddingService`、`VectorStore` 和 `Retriever`，参数取 `settings.top_k`、`settings.max_context_chars`、`settings.min_score`。
- 循环读取输入：直接回车就退出；否则调用 `search`，按下面的格式打印每一条结果。没有结果时打印"没有找到相关资料"。
- 退出时（包括按 Ctrl+C）要 close store。

```text
[1] 0.655  campus/library.md  开放时间
    星河校园图书馆周一至周日每天 8:00 开门，22:00 关门。周末开放时间相同。
```

模型只在程序开头加载一次。加载要 15 秒，而一次查询只要 0.2 到 0.4 秒。

```powershell
.\.venv\Scripts\python.exe -m backend.cli
```

---

## 4. 实验（重点）

### 实验 1：三类问题

逐个提问，记下第 1 条结果是否正确，以及它的分数：

| 类型 | 问题 |
|---|---|
| 资料里有答案 | 图书馆几点关门、补办校园卡要多少钱、网络坏了找谁、有素食吗 |
| 跟校园有关，但资料里没有 | 食堂晚餐几点、宿舍几点熄灯、校医院电话是多少 |
| 完全无关 | 今天天气怎么样、讲个笑话 |

做完后回答：

1. 资料里有答案的问题，第 1 条都找对了吗？
2. 三类问题的第 1 条分数，各自落在什么范围？
3. 能不能找到一个分数线，正好把"有答案"和"没答案"分开？

### 实验 2：TOP_K

现在 `TOP_K=5`，而整个库一共只有 5 条，所以每次都会**把整个库都返回**。看看第 2 到第 5 条的分数和内容，它们对回答有用吗？

把 `.env` 里的 `TOP_K` 改成 `3`，后面就一直用 3。

### 实验 3：MIN_SCORE

在 `.env` 里设置 `MIN_SCORE=0.45`，重启 CLI，再把实验 1 的问题问一遍：

1. 哪一类问题被过滤成了"没有找到相关资料"？
2. 哪一类问题**没有**被过滤掉？换成任何阈值，能解决这个问题吗？
3. 那这一类问题最后要靠谁来处理？

实验做完后，保留 `MIN_SCORE=0.45`。

### 实验 4：追问（为 step9 埋个伏笔）

问"那周末呢"。看看检索到了什么？

假设上一句问的是"食堂几点开饭"，这个检索结果对吗？检索每次只看到当前这一句话。step9 会解决这个问题。

---

## 5. 验收

- [ ] `python -m unittest discover -s backend/tests -t . -v` 全部通过（21 个）
- [ ] CLI 能提问，`.env` 里是 `BATCH_SIZE=8`、`TOP_K=3`、`MIN_SCORE=0.45`
- [ ] 能说清楚：分数是什么？为什么阈值只能过滤一部分没答案的问题？为什么不截断片段？
- [ ] 提交一次 git

对照答案：[step07-answer.md](step07-answer.md)。下一步：[step8：HTTP 接口](step08.md)。
