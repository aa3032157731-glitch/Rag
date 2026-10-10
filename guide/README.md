# RAG 后半程练习（step6–10）

`steps/` 的 step1–5 你已经写完了，代码保留不动。从 step6 开始改用这份练习，`new_step/` 不再使用（它和你现有的代码接不上）。

## 和之前的教程有什么不同

1. **不给完整代码让你抄。** 每一步给出：要解决什么问题、函数接口、规则、测试。实现由你自己写，写完跑测试，通过了再去看 `stepXX-answer.md` 对照。
2. **只保留跟主线有关的代码。** alias 版本切换、manifest、导入锁这些都是生产环境才需要的，先不做。每次删掉一样东西，都会说明它原本是防什么的、以后什么时候需要加回来。
3. **终点是接进数字人项目**，而不是另外新建一个 Unity 工程。数字人里已经有 DeepSeek 调用和聊天界面，`KnowledgeBaseStore.BuildFullContext()` 目前把整个知识库塞进 system 消息。RAG 要做的，就是把这一步换成"按问题检索，只注入相关片段"。

## 路线

| 步骤 | 做成什么 | 新学的东西 |
|---|---|---|
| [step6 导入](step06.md) | 资料 → 向量 → Qdrant | 流水线的顺序、全量重建、upsert 语义 |
| [step7 检索](step07.md) | 命令行提问，打印原文和分数 | top-k、分数的含义、阈值能做什么和做不到什么 |
| [step8 HTTP](step08.md) | FastAPI `/retrieve` | 接口约定、lifespan、`def` 和 `async def`、错误码 |
| [step9 接入数字人](step09.md) | 数字人回答时只用检索到的片段 | 替换全文注入、失败降级、追问 |
| [step10 评估对比](step10.md) | 检索准确率，以及两种模式的对比 | Hit@k、阈值怎么定、怎样判断 RAG 是否真的更好 |

## 每一步怎么做

1. 读完"要解决什么问题"和"规则"，**先自己想一下怎么写**。
2. 把测试文件抄进去（测试就是需求说明）。
3. 写实现，跑测试，直到全部通过。
4. 做"实验"那一节。这一节最重要，别跳过。
5. 看 answer 文件对照。不用逐行一样，重点看顺序和行为是否一致。
6. 有问题，或者写完了想让我看代码，直接问我。

## 参考答案验证到了什么程度

- Python（step6–8、step10）：参考答案在你的 `.venv` 里跑过，29 个测试全部通过；也用真实的 BGE-M3 跑过，教程里的分数、条数和耗时都是实测的。为了不动你 Docker 里的数据库，用的是内存版 Qdrant。
- C#（step9）：`RagRetrievalService` 和 `BuildRagContextAsync` 的逻辑，用 .NET SDK 编译过，并且实际请求过检索服务。**对 VoiceChatController 和设置面板的改动，没有在 Unity 里编译运行过。**
- 没有实际调用过 DeepSeek，所以涉及"数字人会怎么回答"的实验，结果要你自己观察。

## 日常启动顺序（做完 step9 之后）

```powershell
# 1. 打开 Docker Desktop，等它就绪
# 2. Rag 项目根目录：
docker compose --env-file infra/.env -f infra/compose.yaml up -d
.\.venv\Scripts\python.exe -m backend.ingest          # 只有资料或分段参数变了，才需要运行
.\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
# 3. 看到 startup complete 后，在 Unity 里点 Play
```
