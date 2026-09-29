# Step 8：通过 HTTP 提供检索接口

[上一篇](step7.md) · [教程目录](README.md) · [下一篇](step9.md)

## 本篇目标与前置条件

完成第 7 篇，并退出 CLI。Qdrant 继续运行。本篇让其他程序通过 HTTP 调用已经写好的 Retriever；不增加第二套检索逻辑。

最终你会在浏览器里提交一个问题，得到与 CLI 同样结构的原文列表。

## 1. 先理解接口，不急着写代码

HTTP 可以理解为程序之间的请求和响应。这次虽然使用 HTTP，检索服务仍运行在你自己的电脑上。

| 地址 | 用途 |
| --- | --- |
| `GET http://127.0.0.1:8000/health` | 确认检索进程能响应 |
| `POST http://127.0.0.1:8000/retrieve` | 提交问题，返回检索原文 |
| `http://127.0.0.1:8000/docs` | FastAPI 自动生成的交互说明页 |

提交的 JSON：

```json
{"query": "图书馆几点关门？", "top_k": 5}
```

成功响应有三个顶层字段：

| 字段 | 含义 |
| --- | --- |
| `status` | `ok` 或 `no_match` |
| `query` | 实际处理的问题 |
| `results` | 片段列表，每条包含原文、来源和分数 |

`results` 中每条固定包含 `chunk_id/source/title/section/record_key/text/score`。第 10 篇 C# 字段名必须与这里一致。

没有片段时返回 HTTP 200、`status=no_match`、空列表；错误请求返回 422，服务器故障返回非成功状态。错误不是另一种“无答案”。

## 2. 增加 HTTP 依赖

更新 `backend/requirements.txt` 为以下完整内容：

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
fastapi==0.115.12
uvicorn==0.34.3
httpx==0.28.1
```

执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

`FastAPI` 编写接口，`uvicorn` 启动服务，`httpx` 用于本篇的小型接口测试。它们现在才安装，因为前七篇不需要 HTTP。

## 3. 定义请求和响应结构

新建 `backend/app/schemas.py`：

<!-- file: backend/app/schemas.py -->
```python
from typing import Literal
from pydantic import BaseModel, Field


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10, strict=True)


class HitResponse(BaseModel):
    chunk_id: str
    source: str
    title: str
    section: str
    record_key: str
    text: str
    score: float


class RetrieveResponse(BaseModel):
    status: Literal['ok', 'no_match']
    query: str
    results: list[HitResponse]
```

`BaseModel` 让 FastAPI 知道字段类型，并生成浏览器文档。`Field` 约束问题长度和 top_k 范围；`strict=True` 让 top_k 按整数接收，不把布尔值当整数。

全部空白的问题在 Retriever 中统一处理，避免 HTTP 和 CLI 各写一套正文规则。

HTTP 不传 top_k 时使用接口默认值 5；CLI 不传时使用 Settings 的 top_k。Unity 后面会明确传入自己的 topK 设置。

## 4. 写服务入口

新建 `backend/app/main.py`：

<!-- file: backend/app/main.py -->
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request

from backend.app.schemas import RetrieveRequest, RetrieveResponse


def create_app(injected_retriever=None):
    @asynccontextmanager
    async def lifespan(app):
        store = None
        try:
            if injected_retriever is None:
                from backend.app.config import load_settings
                from backend.app.embedding import EmbeddingService
                from backend.app.vector_store import VectorStore
                from backend.app.retriever import Retriever

                settings = load_settings()
                model = EmbeddingService(settings)
                store = VectorStore(settings)
                store.count()
                app.state.retriever = Retriever(settings, model, store)
            else:
                app.state.retriever = injected_retriever
            yield
        finally:
            if store is not None:
                store.close()

    app = FastAPI(title='本机 RAG 检索', lifespan=lifespan)

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.post('/retrieve', response_model=RetrieveResponse)
    def retrieve(body: RetrieveRequest, request: Request):
        try:
            return request.app.state.retriever.retrieve(body.query, body.top_k)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()
```

重点理解 `lifespan` 的上下两部分：

- `yield` 之前：读取配置、加载一次模型、连接数据库、建立 Retriever。
- `yield` 之后：服务停止时关闭数据库连接。

启动时 `store.count()` 确认目标集合可访问。失败就让启动失败，直接查看终端原因；不维护“进程启动了但模型不可用”的额外状态机。[FastAPI 生命周期说明](https://fastapi.tiangolo.com/advanced/events/)

`/health` 是简单的进程响应检查，不持续检查所有依赖。运行中数据库断开时，health 仍可能正常，实际检索会失败，需看终端报错。

`create_app(injected_retriever)` 的可选参数仅供小测试替换检索器；正常启动不传。模型也没有在 import 文件时立刻加载。

## 5. 先启动，再在浏览器提问

终端 A：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

等终端出现启动完成提示。第一次可能要等模型加载；不要加 `--reload` 或多个 worker，这会增加模型重复加载和内存占用。

浏览器打开 `http://127.0.0.1:8000/docs`：

1. 展开 `POST /retrieve`。
2. 点击 Try it out。
3. 在请求框输入本篇开头的 JSON。
4. 点击 Execute。
5. 在 Response body 中找到开放时间原文、来源和分数。

终端 A 保持运行。另开终端 B，也从项目根目录操作：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health'
$body = @{query='校园卡补办要带什么'; top_k=5} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/retrieve' -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

显式编码 UTF-8，避免 PowerShell 版本差异造成中文问题变成乱码。

## 6. 成功后补两项轻量测试

新建 `backend/tests/test_api.py`：

<!-- file: backend/tests/test_api.py -->
```python
import unittest
from fastapi.testclient import TestClient
from backend.app.main import create_app


class ExampleRetriever:
    def retrieve(self, query, top_k):
        if not query.strip():
            raise ValueError('问题不能为空')
        return {'status': 'no_match', 'query': query.strip(), 'results': []}


class ApiTests(unittest.TestCase):
    def test_successful_empty_result(self):
        with TestClient(create_app(ExampleRetriever())) as client:
            self.assertEqual(client.get('/health').status_code, 200)
            response = client.post('/retrieve', json={'query': '问题', 'top_k': 5})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'no_match')
            self.assertEqual(response.json()['results'], [])

    def test_invalid_question_and_top_k(self):
        with TestClient(create_app(ExampleRetriever())) as client:
            for body in [{'query': '   '}, {'query': '问题', 'top_k': 0}]:
                response = client.post('/retrieve', json=body)
                self.assertEqual(response.status_code, 422)


if __name__ == '__main__':
    unittest.main()
```

退出正在运行的服务后，可在终端执行：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_api -v
```

测试用一段固定返回值替代真实检索器，只检查接口能否正确表达“成功但无结果”和“输入不合法”。测试不下载模型、不访问数据库，也不能证明检索效果。

这里使用 HTTPException 和 FastAPI 默认错误格式，不额外设计请求编号、错误 DTO 或全局错误中间件。Python 终端保留具体报错，Unity 显示调用失败即可。

## 为什么这么设计

先把 CLI 查对，再包装成接口，就能确定“资料如何被找到”已经成立。HTTP 这一层只解决程序间通信，后面 Unity 不需要知道模型和数据库内部怎么实现。

模型在启动时加载一次，避免每次请求都等待加载。当前按单人、串行学习使用，不实现多用户调度、限流和高可用机制。

## 排查与验收

| 现象 | 处理 |
| --- | --- |
| 8000 没响应 | 等待模型加载，检查启动终端有没有退出 |
| 422 | 看请求字段、问题是否为空、top_k 是否为 1～10 的整数 |
| 500 | 看 Python 终端；确认 Qdrant、资料集合和模型正常 |
| 改代码没生效 | Ctrl+C 停止服务后重新启动 |
| 超时后重复发送仍很慢 | 超时不代表后端计算结束；先看后端是否完成，避免连续重复请求 |

- [ ] 浏览器可以返回已知问题的原文。
- [ ] API 与 CLI 查到的资料来源一致。
- [ ] 两项接口测试通过。
- [ ] 知道 200 无结果与非成功状态不同。

下一篇先做 Unity 界面，可以暂时停掉 API 节省内存。

---

下一篇：[step9：先做出能输入中文的界面](step9.md)。
