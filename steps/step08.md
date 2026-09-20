# Step 08：编写 FastAPI 检索接口

## 目标与前置条件

完成 step07 并退出 CLI；Qdrant 保持运行。本篇不新增检索算法，只把已经跑通的 Retriever 包装成 HTTP 接口。

HTTP 不代表互联网。`127.0.0.1` 是本机；服务启动后 Unity 可以向它发送 JSON。

## 1. 确定接口契约

新建 `docs/api-contract.md`，填写下列约定（这是文档，不是 Python）：

| 方法与地址 | 行为 |
| --- | --- |
| `GET /health` | 进程可响应，返回 `status=alive` |
| `GET /ready` | 模型和活动索引可用才返回 200，否则 503 |
| `POST /retrieve` | 请求包含 query、top_k，返回结果、版本与耗时 |

成功结果字段：`request_id`、`status`、`query`、`index_version`、`results`、`timings_ms`。results 内有 `chunk_id/source/title/section/record_key/text/score`。

无匹配使用 200 + `status=no_match` + 空 results。错误使用非 2xx 状态码以及 `{"request_id":"...","error":{"code":"...","message":"..."}}`，不混入成功结构。

## 2. 新建 `backend/app/schemas.py`

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

`strict=True` 防止 `true`、`"5"` 被当成合法 top_k。`extra='forbid'` 让拼错字段得到明确错误，不悄悄忽略。

## 3. 新建 `backend/app/main.py`

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
            logger.exception('未处理异常 request_id=%s', request.state.request_id)
            response = failure(request, 500, 'internal_error', '服务内部错误，请查看 request_id 对应日志')
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # 不回显整个问题或输入，防止意外记录私人文本。
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

### 关键解释

- lifespan 在服务开始接受请求前加载模型。首次下载期间 `/health` 也还不能响应，等终端出现启动完成提示后再检查。
- 重型初始化失败时服务仍能报告 health，但 ready=503。模型初始化失败需修复并重启；仅 Qdrant 暂时停止时，恢复数据库后 ready 可恢复。
- 同步 `def` 路由在线程池执行阻塞检索；一把锁限制单次推理，第二个并发请求得到 429，而不是无限排队。
- HTTP 断开不保证终止已经启动的推理，因此 Unity 仍需处理迟到结果。
- `create_app(injected_retriever)` 允许测试注入假检索器，不下载模型。生产启动时不传入它。

参考：[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)、[同步/异步路由](https://fastapi.tiangolo.com/async/)。

## 4. 新建接口测试

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

## 5. 运行服务

终端 A 先跑测试，再启动服务。不要启用 `--reload` 或多个 workers，以免重复加载模型：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_api -v
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

保持终端 A 运行。浏览器打开 `http://127.0.0.1:8000/docs`，展开 POST `/retrieve` → Try it out，填写：

```json
{"query":"图书馆几点关门？","top_k":5}
```

点击 Execute，应该得到包含开放时间原文的 results。网页自带的 curl 命令不需要复制到 PowerShell。

终端 B 在根目录检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health'
Invoke-RestMethod 'http://127.0.0.1:8000/ready'
$body = @{query='校园卡补办要带什么'; top_k=5} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/retrieve' -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

## 6. 排查与验收

| 现象 | 检查与解决 |
| --- | --- |
| 8000 无响应 | 等模型加载结束，检查 uvicorn 是否退出或端口占用 |
| health=200、ready=503 | 看日志；Qdrant、alias、manifest 或模型初始化有问题 |
| 422 | 请求字段名、类型、空白或 token 上限不合法 |
| 429 | 模型正忙，等待上次实际推理结束，不要连续重复点击 |
| 修改 Python 没生效 | 主教程没有 reload，Ctrl+C 后重新启动 |

- [ ] 接口测试通过。
- [ ] 浏览器、CLI 在相同配置下能得到一致来源。
- [ ] 错误状态与无匹配状态不同。
- [ ] 已写明 API 契约，后续 C# 将严格沿用。

下一篇：[step09：创建 Unity 界面](step09.md)。
