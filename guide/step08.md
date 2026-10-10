# Step 8：把检索做成 HTTP 接口

**做完这一步**：运行 `uvicorn` 启动检索服务，浏览器和 PowerShell 都能通过 `POST /retrieve` 查到资料。

**为什么需要这一步**：数字人是 Unity（C#）写的，而 BGE-M3 跑在 Python 里，C# 没法直接调用 Python 的函数。HTTP 就是两者之间的边界：Python 这边只管"问题进、片段出"，Unity 那边只管发请求、收结果。另外，模型只在服务启动时加载一次，之后每次请求只需要零点几秒。

---

## 1. 接口约定（直接照这个做）

这份约定是和 step9 的 Unity 代码共用的，所以先定死，不要改字段名。

```text
POST /retrieve
请求：{"question": "图书馆几点关门", "top_k": 3}      top_k 可以不传，传的话只能是 1～10
200： {"question": "...", "hits": [{"chunk_id", "source", "title", "section", "record_key", "text", "score"}, ...]}
422： question 为空或全是空白、top_k 超出范围、请求体缺少字段
503： Qdrant 连不上

GET /health
200： {"status": "ok"}
```

---

## 2. 练习一：请求和响应的结构

新建 `backend/app/schemas.py`，定义 `RetrieveRequest` 和 `RetrieveResponse` 两个 pydantic 模型。

- 字段约束用 `Field(min_length=..., ge=..., le=...)` 来写。`question` 加一个合理的长度上限，比如 500。
- `hits` 字段的类型直接写 `list[Hit]`。pydantic 能直接使用 `models.py` 里的 dataclass，不用再定义一个一模一样的类。

**想一想**：`"   "`（三个空格）能通过 `min_length=1` 吗？如果能通过，那该由谁来拒绝它？

---

## 3. 练习二：服务入口

新建 `backend/api.py`：

```python
def create_app(retriever=None):
    """创建并返回 FastAPI 应用。测试时传入假的 retriever，正式运行时不传。"""

app = create_app()      # uvicorn 启动的就是这个对象
```

**规则**

- 不传 `retriever` 时，在启动阶段读配置，创建模型、store 和 Retriever；在关闭阶段 close store。传了 `retriever` 就直接用。
- 两个接口都写成普通的 `def`，**不要写成 `async def`**。原因见下面。
- 两种异常要转换：`ValueError` 转成 422；Qdrant 的 `ResponseHandlingException` 和 `UnexpectedResponse` 转成 503。这两个异常在 `qdrant_client.http.exceptions` 里。
- **不要捕获所有异常。** 如果是你自己代码里的 bug，应该让它变成 500 并且带着 traceback 暴露出来，而不是伪装成"数据库不可用"。

**启动和关闭时要做的事**，用 FastAPI 的 lifespan 写。骨架如下：

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    app.state.retriever = ...   # 启动时执行：加载模型
    yield                       # 服务运行期间停在这里
    ...                         # 关闭时执行：释放资源

app = FastAPI(lifespan=lifespan)
```

在接口函数里，用 `app.state.retriever` 取到它。

**为什么用 `def` 而不是 `async def`？** 一次检索要在 CPU 上算 0.3 秒左右，这是阻塞操作。写成普通 `def`，FastAPI 会把它放到线程池里执行；写成 `async def`，它就会直接在事件循环里执行，这 0.3 秒里服务器什么请求都处理不了，连 `/health` 都会卡住。

我在你的机器上测过，8 个线程同时编码问题不会出错，所以这里不需要加锁。

### 用测试检查

新建 `backend/tests/test_api.py`：

```python
import unittest

from fastapi.testclient import TestClient
from qdrant_client.http.exceptions import ResponseHandlingException

from backend.api import create_app
from backend.app.models import Hit


class FakeRetriever:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def search(self, question, top_k=None):
        self.calls.append((question, top_k))
        if self.error:
            raise self.error
        if not question.strip():
            raise ValueError('问题不能为空')
        return [Hit('id1', 'campus/library.md', '图书馆', '开放时间', 'md:0', '每天 8:00 开门。', 0.66)]


class ApiTests(unittest.TestCase):
    def client(self, retriever):
        return TestClient(create_app(retriever))

    def test_retrieve_returns_hits(self):
        fake = FakeRetriever()
        with self.client(fake) as client:
            response = client.post('/retrieve', json={'question': '图书馆几点开门', 'top_k': 3})
        self.assertEqual(response.status_code, 200)
        hit = response.json()['hits'][0]
        self.assertEqual(hit['source'], 'campus/library.md')
        self.assertEqual(hit['text'], '每天 8:00 开门。')
        self.assertEqual(fake.calls, [('图书馆几点开门', 3)])

    def test_bad_requests_are_422(self):
        with self.client(FakeRetriever()) as client:
            self.assertEqual(client.post('/retrieve', json={'question': ''}).status_code, 422)
            self.assertEqual(client.post('/retrieve', json={'question': '   '}).status_code, 422)
            self.assertEqual(client.post('/retrieve', json={'question': '你好', 'top_k': 0}).status_code, 422)
            self.assertEqual(client.post('/retrieve', json={}).status_code, 422)

    def test_database_down_is_503(self):
        fake = FakeRetriever(error=ResponseHandlingException(ConnectionError('refused')))
        with self.client(fake) as client:
            response = client.post('/retrieve', json={'question': '图书馆'})
        self.assertEqual(response.status_code, 503)

    def test_health(self):
        with self.client(FakeRetriever()) as client:
            self.assertEqual(client.get('/health').json(), {'status': 'ok'})


if __name__ == '__main__':
    unittest.main()
```

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_api -v
```

---

## 4. 启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

大约 15 秒后会看到 `Application startup complete`，这时服务就可以用了。

- **不要加 `--reload`。** 它会在你每次保存文件时重启服务，于是每次都要重新加载 15 秒的模型。
- 浏览器打开 <http://127.0.0.1:8000/docs>，点 `POST /retrieve` → Try it out，填入问题后执行。这是测试接口最省事的方法，中文也没有问题。

---

## 5. 用 PowerShell 调用（有坑，先看）

你用的 Windows PowerShell 5.1 在中文上有两个坑，我在你的机器上都实测到了。

**坑 1：直接把 JSON 字符串当 body 发，中文会变成问号，而且不会报错。**

```powershell
# 错误写法：服务端收到的问题是 "?????????"，于是返回 0 条结果
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/retrieve -ContentType 'application/json' -Body '{"question":"补办校园卡要多少钱"}'
```

**坑 2：就算请求发对了，`Invoke-RestMethod` 显示的结果也是乱码。** 原因是 FastAPI 返回的 `Content-Type` 里没有写 `charset`，PowerShell 5.1 就不会按 UTF-8 来解码。

正确的写法是：自己把请求体转成 UTF-8 字节，再自己把响应按 UTF-8 解码：

```powershell
$body = [Text.Encoding]::UTF8.GetBytes('{"question":"图书馆几点关门","top_k":3}')
$r = Invoke-WebRequest -Method Post -Uri 'http://127.0.0.1:8000/retrieve' -ContentType 'application/json' -Body $body -UseBasicParsing
$json = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
$json.hits | Format-List score, title, section, text
```

Unity 那边不会有这个问题，因为 C# 的 `StringContent(..., Encoding.UTF8, ...)` 会明确按 UTF-8 发送，这一点我也用 C# 实测过。这个坑告诉我们：**两个程序之间传文字，编码一定要在两头都写明确。**

---

## 6. 实验

### 实验 1：为什么要在启动时加载模型

1. 记一下从启动到出现 `startup complete` 用了多久，再记一下一次请求用了多久。
2. 想一想：如果把创建 `EmbeddingService` 的代码挪到 `retrieve()` 函数里面，每次请求要多久？数字人每说一句话都要等这么久，能接受吗？

### 实验 2：Qdrant 挂了

1. 服务保持运行，执行 `docker compose --env-file infra/.env -f infra/compose.yaml stop`。
2. 在 /docs 里请求一次，看状态码和 `detail` 里写了什么。
3. 执行 `start` 把 Qdrant 恢复，再请求一次。需要重启 API 吗？

### 实验 3：看 422 的内容

在 /docs 里把 `top_k` 填成 20 再请求，读一读返回的错误内容。它准确地指出了是哪个字段、违反了什么规则。在边界上校验输入的好处就在这里：Unity 那边写错了，看一眼报错就知道问题在哪。

---

## 7. 验收

- [ ] 全部测试通过（25 个）
- [ ] /docs 里能查到资料，PowerShell 的正确写法也能用
- [ ] 能说清楚：为什么在 lifespan 里加载模型？为什么接口用 `def`？为什么不捕获所有异常？
- [ ] 提交一次 git

对照答案：[step08-answer.md](step08-answer.md)。下一步：[step9：接入数字人](step09.md)。
