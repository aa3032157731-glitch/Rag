# Step 08：编写 FastAPI 检索接口

> **本节改动说明**：全局异常中间件记录日志时，除了原来的 `request_id`，现在还会带上 `method` 和 `path`。以前排查一条报错日志得先反查 `request_id` 才知道是哪个接口出的问题，现在日志里直接就有。其余接口行为、返回结构与原教程完全一致。

## 本节目标

完成后你能：通过浏览器或任何 HTTP 客户端调用 `/retrieve`，得到和 step07 命令行一致的检索结果；服务未就绪、参数错误、并发过载这几种情况都有明确区分的状态码。

**前置条件**：完成 step07 并退出 CLI，Qdrant 保持运行。本篇不新增任何检索算法，只是把已经跑通的 `Retriever` 包装成 HTTP 接口。

`127.0.0.1` 指的是本机，不是互联网——搞清楚这一点，后面 Unity 通过 HTTP 调用这个服务时才不会有"是不是要联网"的疑惑。

## 一、先确定接口契约

新建 `docs/api-contract.md`（这是一份文档，不是代码），先把约定写清楚再动手实现，能少走很多弯路：

| 方法与地址 | 行为 |
| --- | --- |
| `GET /health` | 进程能响应就返回 `status=alive`，不检查依赖 |
| `GET /ready` | 模型和活动索引都可用才返回 200，否则 503 |
| `POST /retrieve` | 请求包含 `query`、`top_k`，返回结果、索引版本和各阶段耗时 |

成功响应字段：`request_id`、`status`、`query`、`index_version`、`results`、`timings_ms`；`results` 里每条包含 `chunk_id`/`source`/`title`/`section`/`record_key`/`text`/`score`。

**无匹配**用 200 + `status=no_match` + 空 `results`。**错误**用非 2xx 状态码，body 为 `{"request_id":"...","error":{"code":"...","message":"..."}}`——这两种结构绝对不能混用，客户端要能一眼判断"这次调用到底算不算成功"。

## 二、新建 `backend/app/schemas.py`

<!-- file: backend/app/schemas.py -->
```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=2000, strict=True)
    top_k: int = Field(default=5, ge=1, le=10, strict=True)

    @field_validator('query')
    @classmethod
    def strip_query(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('问题不能为空白')
        return value


class HitResponse(BaseModel):
    chunk_id: str
    source: str
    title: str
    section: str
    record_key: str
    text: str
    score: float


class Timings(BaseModel):
    embedding: float
    search: float
    total: float


class RetrieveResponse(BaseModel):
    request_id: str
    status: Literal['ok', 'no_match']
    query: str
    index_version: str
    results: list[HitResponse]
    timings_ms: Timings
```

`strict=True` 能防止 `true`、`"5"` 这类值被当成合法的 `top_k`（Pydantic 默认的"宽松"模式会尝试把字符串转成数字，strict 模式关掉这种自动转换）。`extra='forbid'` 让请求里如果不小心拼错字段名，会得到明确的 422 错误，而不是被悄悄忽略。

## 三、新建 `backend/app/main.py`

<!-- file: backend/app/main.py -->
```python
from contextlib import asynccontextmanager
import logging
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.app.models import ServiceUnavailable
from backend.app.schemas import RetrieveRequest, RetrieveResponse


logger = logging.getLogger('rag')


def create_app(injected_retriever=None):
    @asynccontextmanager
    async def lifespan(app):
        store = None
        app.state.retriever = injected_retriever
        app.state.gate = Lock()
        if injected_retriever is None:
            try:
                from backend.app.config import load_settings
                from backend.app.embedding import EmbeddingService
                from backend.app.vector_store import VectorStore
                from backend.app.retriever import Retriever
                settings = load_settings()
                model = EmbeddingService(settings)
                store = VectorStore(settings)
                app.state.retriever = Retriever(settings, model, store)
            except Exception:
                logger.exception('模型或服务初始化失败；修复后重启进程')
        try:
            yield
        finally:
            if store is not None:
                store.close()

    app = FastAPI(title='本地 RAG 检索', lifespan=lifespan)

    def failure(request, status, code, message):
        return JSONResponse(status_code=status, content={
            'request_id': request.state.request_id,
            'error': {'code': code, 'message': message},
        })

    @app.middleware('http')
    async def request_id(request: Request, call_next):
        request.state.request_id = uuid4().hex
        try:
            response = await call_next(request)
        except Exception:
            # 带上 method/path，排查时不用先反查 request_id 才知道是哪个接口报的错。
            logger.exception(
                '未处理异常 request_id=%s method=%s path=%s',
                request.state.request_id, request.method, request.url.path,
            )
            response = failure(request, 500, 'internal_error', '服务内部错误，请查看 request_id 对应日志')
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # 不回显整个问题或输入内容，防止意外把私人文本记进日志。
        return failure(request, 422, 'invalid_request', 'query 必须为非空文字，top_k 必须为 1～10 的整数')

    @app.get('/health')
    def health():
        return {'status': 'alive'}

    @app.get('/ready')
    def ready(request: Request):
        retriever = app.state.retriever
        if retriever is None:
            return failure(request, 503, 'not_ready', '模型未加载，请检查服务日志并重启')
        try:
            return {'status': 'ready', 'index_version': retriever.ready()}
        except ServiceUnavailable as exc:
            return failure(request, 503, 'not_ready', str(exc))

    @app.post('/retrieve', response_model=RetrieveResponse)
    def retrieve(body: RetrieveRequest, request: Request):
        retriever = app.state.retriever
        if retriever is None:
            return failure(request, 503, 'not_ready', '模型未加载')
        if not app.state.gate.acquire(blocking=False):
            return failure(request, 429, 'busy', '模型正在处理其他请求，请稍后重试')
        try:
            data = retriever.retrieve(body.query, body.top_k)
            return {'request_id': request.state.request_id, **data}
        except ValueError as exc:
            return failure(request, 422, 'invalid_query', str(exc))
        except ServiceUnavailable as exc:
            return failure(request, 503, 'dependency_unavailable', str(exc))
        finally:
            app.state.gate.release()

    return app


app = create_app()
```

## 四、关键代码解读

- **lifespan 在服务真正开始接受请求之前加载模型**：首次下载期间连 `/health` 都还不会响应，等终端里出现启动完成的提示后再去检查。
- **初始化失败不等于进程崩溃**：`lifespan` 里 `except Exception` 只是记录日志，`app.state.retriever` 保持 `None`——这样 `/health` 还能正常返回，方便你确认"进程活着，但模型没起来"，而 `/ready` 会明确返回 503。
- **单把锁 = 单次推理**：因为整个进程只有一份模型实例（内存有限），这里用一把 `Lock` 保证同一时刻只处理一个 `/retrieve` 请求，第二个并发请求会立刻拿到 429，而不是排队等到超时——这是"明确拒绝"优于"隐式变慢"的一个例子。如果以后想支持更高并发，需要先解决"多份模型实例的显存/内存开销"这个前提问题，不是简单加大锁的粒度就能解决的。
- **HTTP 断开不等于计算停止**：Python 后端已经开始的推理不会因为客户端断开连接就立刻终止，所以 Unity 那边（step10）仍然需要自己处理"迟到的结果"。
- **`create_app(injected_retriever)`**：允许测试注入一个假的检索器，跳过真实模型加载；正式启动时不传这个参数。

参考文档：[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)、[同步/异步路由](https://fastapi.tiangolo.com/async/)。

## 五、新建接口测试

文件：`backend/tests/test_api.py`。

<!-- file: backend/tests/test_api.py -->
```python
import unittest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models import ServiceUnavailable


class FakeRetriever:
    def ready(self):
        return 'rag_test'
    def retrieve(self, query, top_k):
        if query == '断开':
            raise ServiceUnavailable('模拟数据库断开')
        return {
            'status': 'no_match', 'query': query,
            'index_version': 'rag_test', 'results': [],
            'timings_ms': {'embedding': 0, 'search': 0, 'total': 0},
        }


class ApiTests(unittest.TestCase):
    def test_health_ready_and_no_match(self):
        with TestClient(create_app(FakeRetriever())) as client:
            self.assertEqual(client.get('/health').status_code, 200)
            self.assertEqual(client.get('/ready').json()['index_version'], 'rag_test')
            response = client.post('/retrieve', json={'query': '问题', 'top_k': 5})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'no_match')
            self.assertEqual(response.headers['X-Request-ID'], response.json()['request_id'])

    def test_validation_dependency_and_busy(self):
        app = create_app(FakeRetriever())
        with TestClient(app) as client:
            for body in [
                {'query': '   '}, {'query': '问题', 'top_k': 0},
                {'query': '问题', 'top_k': True}, {'query': '问题', 'typo': 1},
            ]:
                response = client.post('/retrieve', json=body)
                self.assertEqual(response.status_code, 422)
                self.assertIn('error', response.json())
            self.assertEqual(client.post('/retrieve', json={'query': '断开'}).status_code, 503)
            app.state.gate.acquire()
            try:
                self.assertEqual(client.post('/retrieve', json={'query': '问题'}).status_code, 429)
            finally:
                app.state.gate.release()


if __name__ == '__main__':
    unittest.main()
```

## 六、运行服务

终端 A 先跑测试，再启动服务。不要加 `--reload`、也不要开多个 worker，否则会重复加载模型：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_api -v
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

保持这个终端运行，浏览器打开 `http://127.0.0.1:8000/docs`，展开 `POST /retrieve` → Try it out，填入：

```json
{"query":"图书馆几点关门？","top_k":5}
```

点击 Execute，应该能拿到包含开放时间原文的 `results`（网页自带生成的 curl 命令不用管，那是给别的环境用的）。

终端 B 在项目根目录检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health'
Invoke-RestMethod 'http://127.0.0.1:8000/ready'
$body = @{query='校园卡补办要带什么'; top_k=5} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/retrieve' -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

## 七、排查与验收

| 现象 | 检查与解决 |
| --- | --- |
| 8000 端口无响应 | 等模型加载完成；检查 uvicorn 是不是意外退出，或端口被占用 |
| `health=200` 但 `ready=503` | 查日志——大概率是 Qdrant、alias、manifest 或模型初始化出了问题 |
| 收到 422 | 检查请求字段名、类型、是不是空白字符串，或者问题长度超过上限 |
| 收到 429 | 模型正忙，等上一次真实推理结束即可，不要连续重复点击加重排队 |
| 改了 Python 代码没生效 | 本教程没有开 `--reload`，需要 Ctrl+C 后重新启动 |

- [ ] 接口测试全部通过。
- [ ] 浏览器和 CLI 在相同配置下能得到一致的来源。
- [ ] 错误状态和无匹配状态返回的结构明显不同。
- [ ] `docs/api-contract.md` 已写好，C# 端会严格照着这份契约来写。

下一篇：[step09：创建 Unity 界面](step09.md)。
