//# Step 6：把资料导入 Qdrant

**做完这一步**：运行 `python -m backend.ingest`，`data/documents` 里的资料变成 `rag_tutorial` 集合里的 5 条向量。重复运行还是 5 条；修改资料后再运行，库里只剩新内容。

**用到的已有代码**：`load_documents`、`split_document`、`fit_token_budget`、`embedding_text`（step2–3），以及 `EmbeddingService`（step4）。

这一步没有新概念要背。重点是**把它们按正确的顺序串起来，并且弄明白顺序为什么重要**。

---

## 0. 先做减法：去掉 alias 版本切换

旧 step5 的 VectorStore 有 alias 和 manifest，旧 step6 还要加导入锁。它们各自防的是：

| 机制 | 防什么 | 你现在会遇到吗 |
|---|---|---|
| alias 切换 | 导入进行到一半时有人查询，查到半成品 | 不会。导入是你手动运行的，运行期间不去查询就行 |
| manifest | 记录索引是用哪个模型生成的，防止查询时用错模型 | 不会。只有一个模型 |
| 导入锁 | 两个人同时导入 | 不会。只有你一个人 |

去掉之后要付出的代价是：导入的那十几秒里查询会失败；导入中途崩溃了要重新运行。对本机学习项目来说完全可以接受。等以后数字人要长期在线、资料要边用边更新时，再把 alias 加回来，那时你会清楚它为什么存在。

旧 `vector_store.py` 里的两个 bug（第 65 行 `CreateAlias` 没有包成 `CreateAliasOperation`，第 117 行把 `aliases` 拼成了 `alises`）会随 alias 一起删掉。

## 1. 改配置（直接照做）

`backend/app/config.py` 里的

```python
    qdrant_alias: str = 'rag_active'
```

改成

```python
    qdrant_collection: str = 'rag_tutorial'
```

`backend/.env` 和 `backend/.env.example` 两个文件里的 `QDRANT_ALIAS=rag_active` 都改成 `QDRANT_COLLECTION=rag_tutorial`。

> 你的 Settings 设置了 `extra='forbid'`，`.env` 里出现 Settings 不认识的字段就会在启动时报错，所以这两处要一起改。这个报错是好事：字段名拼错了会马上被发现，而不是悄悄用了默认值。

再删掉 `data/documents/campus/__init__.py`。资料目录不是 Python 包，这个文件只会让 loader 多报一条"跳过"。

检查：

```powershell
.\.venv\Scripts\python.exe -m backend.app.config
```

输出里应该有 `"qdrant_collection": "rag_tutorial"`。

---

## 2. 练习一：重写 VectorStore

清空 `backend/app/vector_store.py`，按下面的接口重写。一个 VectorStore 对象只管一个集合，集合名在创建对象时就确定了。

```python
class VectorStore:
    def __init__(self, settings, client=None, name=None): ...
    def recreate(self, dimension): ...      # 集合存在就先删掉，再新建一个空集合
    def upsert(self, chunks, vectors): ...  # 第 i 个 chunk 配第 i 个向量，写进去
    def query(self, vector, limit): ...     # 返回最相近的 limit 个点（要带 payload）
    def count(self): ...                    # 集合里有几个点
    def close(self): ...
```

**规则**

- 不传 `name` 时用 `settings.qdrant_collection`；不传 `client` 时自己连接 `settings.qdrant_url`。`client` 参数是给测试用的：测试会传入内存版的 Qdrant，不需要开 Docker。
- 每个点的 id 是 `chunk.chunk_id`，vector 是对应的向量，payload 是 `chunk.payload()`。
- chunks 和 vectors 数量不一样时，抛出 `ValueError`。
- 距离用 Cosine。

**提示**：要用到 `self.client` 上的这几个方法：`collection_exists`、`delete_collection`、`create_collection`、`upsert`、`query_points`、`count`。参数不确定的话，在 PyCharm 里按住 Ctrl 点击方法名就能看到签名；也可以翻 git 里旧的 `vector_store.py`，大部分写法可以直接搬过来。

**写之前先想两个问题**（答案在 answer 文件里）：

1. `upsert` 为什么要检查长度？如果去掉检查，传 3 个 chunk 和 2 个向量，会发生什么？（提示：`zip`）
2. payload 为什么要存整个 chunk，而不是只存 `text`？想一想数字人回答问题时要显示什么。

### 用测试检查

新建 `backend/tests/test_vector_store.py`：

```python
import unittest
from types import SimpleNamespace

from qdrant_client import QdrantClient

from backend.app.loader import make_document
from backend.app.splitter import split_document
from backend.app.vector_store import VectorStore


def make_chunk(key, text):
    doc = make_document('test.txt', '测试', '', key, text)
    return split_document(doc)[0]


class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        settings = SimpleNamespace(qdrant_collection='test_store')
        self.store = VectorStore(settings, client=QdrantClient(':memory:'))
        self.store.recreate(3)
        self.a = make_chunk('a', '图书馆八点开门')
        self.b = make_chunk('b', '食堂二楼有素食')

    def tearDown(self):
        self.store.close()

    def test_nearest_point_returns_its_text(self):
        self.store.upsert([self.a, self.b], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        rows = self.store.query([0.9, 0.1, 0.0], 1)
        self.assertEqual(rows[0].payload['text'], '图书馆八点开门')
        self.assertEqual(rows[0].payload['source'], 'test.txt')

    def test_same_id_overwrites_instead_of_adding(self):
        self.store.upsert([self.a], [[1.0, 0.0, 0.0]])
        self.store.upsert([self.a], [[1.0, 0.0, 0.0]])
        self.assertEqual(self.store.count(), 1)

    def test_recreate_empties_collection(self):
        self.store.upsert([self.a, self.b], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        self.store.recreate(3)
        self.assertEqual(self.store.count(), 0)

    def test_length_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.upsert([self.a, self.b], [[1.0, 0.0, 0.0]])


if __name__ == '__main__':
    unittest.main()
```

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_vector_store -v
```

4 个测试全部 ok 再往下做。留意 `test_same_id_overwrites_instead_of_adding`：**同一个 id 写两次，只算一条**。第 6 节的实验会用到这一点。

---

## 3. 练习二：修好 check_qdrant.py

`check_qdrant.py` 调用的还是旧接口，现在已经跑不起来了。把它改成新接口，同时修一个 bug：

> 现在第 20 行的 `store.client.upsert(...)` 写在了 `if not args.name:` 外面。带 `--name` 运行时，它会先把数据重新写一遍再查询，所以"重启后还能查到"并不能证明数据真的保存下来了。写入应该只在新建集合时进行。

要点：用 `VectorStore(load_settings(), name=...)` 创建对象；新建集合时调用 `recreate(3)`；查询用 `store.query(...)`。

验证（Docker 里的 Qdrant 要在运行）：

```powershell
docker compose --env-file infra/.env -f infra/compose.yaml up -d
.\.venv\Scripts\python.exe -m backend.scripts.check_qdrant
docker compose --env-file infra/.env -f infra/compose.yaml restart
.\.venv\Scripts\python.exe -m backend.scripts.check_qdrant --name 换成上一步打印出来的名字
```

---

## 4. 练习三：写 ingest.py

新建 `backend/ingest.py`，写三个函数：

```python
def load_chunks(settings):
    """读资料，再按字符切分，返回 list[Chunk]。
    资料有错误，或者一个片段都没有时，抛出 ValueError。"""

def ingest(chunks, model, store, max_tokens):
    """按 token 再细分 → 编码 → 重建集合 → 写入。返回写入后的条数。"""

def main():
    """读配置，创建真实的 EmbeddingService 和 VectorStore，调用上面两个函数，打印结果。
    最后记得 close store。"""
```

`ingest` 只用到 model 和 store 的几个方法（`model.count_tokens`、`model.encode_documents`、`model.dimension`，以及 store 的 `recreate`、`upsert`、`count`），并不关心它们是真模型还是假模型。所以测试时可以传入一个假模型，几毫秒就能跑完，不用每次都等 BGE-M3 加载十几秒。

### 这一步真正要想清楚的是顺序

下面每条规则说的都只是"哪行代码放在哪行前面"，不需要额外写任何代码：

1. **先读资料，再加载模型。** 模型加载要十几秒。资料里有一个 JSON 写错了，应该马上报错，而不是等模型加载完才发现。所以 `main` 里要先调用 `load_chunks`。
2. **先编码，再重建集合。** `recreate` 会删掉旧数据。如果先删再编码，编码中途一出错，库就空了；反过来，编码失败时旧数据还在。
3. **token 细分放在这里，不放在 splitter 里。** 数 token 要用模型的 tokenizer，而 splitter 不应该依赖模型。所以 splitter 只提供 `fit_token_budget(chunks, count_tokens, limit)`，由这里把 `model.count_tokens` 传进去。

### 用测试检查

新建 `backend/tests/test_ingest.py`：

```python
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from qdrant_client import QdrantClient

from backend.app.vector_store import VectorStore
from backend.ingest import load_chunks, ingest


class FakeModel:
    """假模型：不加载 BGE-M3，用字符数代替 token 数，所有向量都一样。"""
    dimension = 3

    def count_tokens(self, text):
        return len(text)

    def encode_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


class BrokenModel(FakeModel):
    def encode_documents(self, texts):
        raise RuntimeError('模拟编码失败')


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = SimpleNamespace(
            documents_dir=self.root, chunk_size=600, chunk_overlap=100,
            qdrant_collection='test_ingest',
        )
        self.store = VectorStore(self.settings, client=QdrantClient(':memory:'))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def write(self, name, text):
        (self.root / name).write_text(text, encoding='utf-8')

    def test_running_twice_does_not_double(self):
        self.write('a.txt', '图书馆八点开门。')
        self.write('b.txt', '食堂二楼有素食。')
        chunks = load_chunks(self.settings)
        self.assertEqual(ingest(chunks, FakeModel(), self.store, 1024), 2)
        self.assertEqual(ingest(chunks, FakeModel(), self.store, 1024), 2)

    def test_deleted_file_disappears_after_ingest(self):
        self.write('a.txt', '图书馆八点开门。')
        self.write('b.txt', '食堂二楼有素食。')
        ingest(load_chunks(self.settings), FakeModel(), self.store, 1024)
        (self.root / 'b.txt').unlink()
        self.assertEqual(ingest(load_chunks(self.settings), FakeModel(), self.store, 1024), 1)

    def test_bad_document_stops_before_model(self):
        self.write('bad.json', '{')
        with self.assertRaises(ValueError):
            load_chunks(self.settings)

    def test_encode_failure_keeps_old_data(self):
        self.write('a.txt', '图书馆八点开门。')
        chunks = load_chunks(self.settings)
        ingest(chunks, FakeModel(), self.store, 1024)
        with self.assertRaises(RuntimeError):
            ingest(chunks, BrokenModel(), self.store, 1024)
        self.assertEqual(self.store.count(), 1)

    def test_long_text_is_split_by_token_budget(self):
        self.write('long.txt', '甲乙丙丁' * 50)
        count = ingest(load_chunks(self.settings), FakeModel(), self.store, 64)
        self.assertGreater(count, 1)


if __name__ == '__main__':
    unittest.main()
```

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_ingest -v
```

`test_encode_failure_keeps_old_data` 检查的就是规则 2。可以故意把 `recreate` 挪到编码前面，看它会不会失败。

---

## 5. 真正导入一次

```powershell
.\.venv\Scripts\python.exe -m backend.ingest
(Invoke-RestMethod 'http://127.0.0.1:6333/collections/rag_tutorial').result.points_count
```

预期结果：字符分段 5，导入完成 5 条，`points_count` 也是 5。中间会看到 FlagEmbedding 打印的 `pre tokenize` 和 `Inference Embeddings` 进度条，这是正常的。模型加载大约需要 15 秒，这是在你这台机器上用 CPU 实测的。

浏览器打开 <http://127.0.0.1:6333/dashboard>，点进 `rag_tutorial`，看看每个点的 payload 里存了什么。

---

## 6. 实验（这一节最重要，别跳过）

### 实验 1：为什么一定要"重建"，而不能直接写

1. 把 `ingest()` 里 `store.recreate(...)` 那一行临时注释掉。
2. 运行两次 ingest。条数是多少？为什么？
3. 把 `library.md` 里的 `22:00` 改成 `21:00`，再运行一次。条数变成了几？为什么？
4. 想一想：这时问"图书馆几点关门"，检索会找到什么？数字人会怎么回答？
5. 恢复 `recreate` 那一行，把时间改回 `22:00`，重新导入，确认回到 5 条。

提示：看看 `splitter.py` 里的 `chunk_id` 是用哪些东西算出来的。

### 实验 2：token 预算切得太碎会怎样

1. 把 `backend/.env` 里的 `MAX_TOKENS` 改成 32，运行 ingest。条数变成了几？
2. 在 dashboard 里看看被切出来的片段。哪些片段单独拿出来已经看不懂了？如果有人问"图书馆几点关门"，哪一段能回答？
3. 改回 1024，重新导入。

### 实验 3：Qdrant 没开的时候

1. 运行 `docker compose --env-file infra/.env -f infra/compose.yaml stop`，再运行 ingest。
2. 错误出现在哪一步？你等了多久才看到错误？
3. 如果想更早发现问题，可以怎么改？（想一想就行，不用改）
4. 运行 `docker compose --env-file infra/.env -f infra/compose.yaml start` 恢复。

---

## 7. 验收

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -t . -v
```

- [ ] 全部测试通过（loader、splitter、vector_store、ingest 一共 16 个）
- [ ] `rag_tutorial` 里有 5 条数据，资料已经恢复原样
- [ ] 能用自己的话讲清楚：为什么要先编码再重建？不重建为什么会留下脏数据？payload 为什么要存整个 chunk？
- [ ] 提交一次 git

做完了，再打开 [step06-answer.md](step06-answer.md) 对照。

## 下一步预告

[step7](step07.md) 是命令行检索：问题 → `encode_query` → `store.query` → 打印原文和分数。

你会看到一个有意思的现象：问"食堂晚餐几点"，检索会以 0.68 分找到食堂那一段，可资料里明明写着"没有提供晚餐时间"。**检索只负责找"相关"的内容，不负责判断"能不能回答"。** step7 会讨论这个问题。
