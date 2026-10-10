# Step 8 参考答案

下面的代码已经跑过：测试全部通过；也用真实模型启动过服务，分别用 PowerShell 5.1 和 C# 客户端实际调用过。

## backend/app/schemas.py

```python
from pydantic import BaseModel, Field

from backend.app.models import Hit


class RetrieveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=10)


class RetrieveResponse(BaseModel):
    question: str
    hits: list[Hit]
```

## backend/api.py

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from backend.app.config import load_settings
from backend.app.embedding import EmbeddingService
from backend.app.retriever import Retriever
from backend.app.schemas import RetrieveRequest, RetrieveResponse
from backend.app.vector_store import VectorStore


def create_app(retriever=None):
    @asynccontextmanager
    async def lifespan(app):
        if retriever is not None:
            app.state.retriever = retriever
            yield
            return
        settings = load_settings()
        store = VectorStore(settings)
        app.state.retriever = Retriever(
            EmbeddingService(settings), store,
            settings.top_k, settings.max_context_chars, settings.min_score,
        )
        yield
        store.close()

    app = FastAPI(title='RAG 检索服务', lifespan=lifespan)

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.post('/retrieve', response_model=RetrieveResponse)
    def retrieve(request: RetrieveRequest):
        try:
            hits = app.state.retriever.search(request.question, request.top_k)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except (ResponseHandlingException, UnexpectedResponse) as exc:
            raise HTTPException(status_code=503, detail=f'向量数据库不可用：{exc}')
        return RetrieveResponse(question=request.question, hits=hits)

    return app


app = create_app()
```

几点说明：

- **`app = create_app()` 写在模块顶层，import 时不会加载模型。** lifespan 只在服务真正启动时才执行，所以测试 import `backend.api` 也很快。
- **为什么用 `create_app()` 函数，而不是直接写一个全局 app？** 这样测试可以把假的 retriever 传进去，测试就不需要模型，也不需要 Qdrant。step6 的 `ingest(chunks, model, store, ...)` 也是同样的思路：把依赖从外面传进来。
- **`"   "` 能通过 `min_length=1`**，因为空格也算字符。它是被 `Retriever.search` 里的 `strip()` 检查拦下来的，再由 `ValueError` 转成 422。校验分成了两层：格式问题由 pydantic 负责，含义问题由业务代码负责。

---

## 实验

### 实验 1：在启动时加载模型

实测：启动要等大约 15 秒；之后每次请求，用 C# 客户端测是 0.3 秒左右（第一次请求 0.6 秒，因为要建立连接、预热）。

如果把模型挪到 `retrieve()` 里，每次请求都要 15 秒以上，数字人每句话之前都要发呆十几秒。lifespan 的作用就是"昂贵的东西只做一次"。

### 实验 2：Qdrant 挂了

接口会返回 503，`detail` 里带着 Qdrant 客户端报的原因。在你的机器上，原因很可能显示为 `502 (Bad Gateway)`，这是 step6 实验 3 里提到的系统代理导致的。

Qdrant 恢复之后，**一般不需要重启 API**。客户端每次请求都会从连接池里取连接，旧连接断了就会重新建立。如果恢复后的第一次请求还是 503，再请求一次就好。（这一步我没能在你的机器上实测，因为没有动你的 Docker。）

把 503 和 500 分开有实际意义：Unity 收到 503，就知道"检索服务在，但数据库没开"；收到 500，就知道是代码有 bug。step9 会把这些信息显示在数字人的知识库状态栏里。

### 实验 3：422

返回的内容大致是：

```json
{"detail": [{"type": "less_than_equal", "loc": ["body", "top_k"], "msg": "Input should be less than or equal to 10", "input": 20, ...}]}
```

`loc` 指出了是哪个字段，`msg` 说明了违反的是什么规则。这些是 pydantic 自动生成的，你不需要写任何代码。
