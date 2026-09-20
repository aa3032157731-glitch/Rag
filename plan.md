# 从零手写 RAG：Python 后端 + Unity C# 客户端实施计划

> 项目目录：`C:\Users\qqcom\PycharmProjects\Rag`
>
> 编写日期：2026-09-16。
>
> 本文是学习与实施计划，不是已经实现的系统。按阶段手敲代码，每完成一个阶段先验证，再进入下一阶段。文中的接口、命令行参数和配置字段是待实现的约定；对应文件写好后才能运行。

## 1. 最终要做出的系统

从空白业务代码开始，建立独立于原数字人项目的 RAG 系统：

1. 将自己的 TXT、Markdown、JSON 文档放入指定目录。
2. Python 读取文档、拆分片段，通过本地 BGE-M3 生成向量。
3. 将向量与原文、来源信息存入本地 Qdrant。
4. 在新的 Unity 客户端输入问题。
5. Unity 请求本机 FastAPI 检索接口。
6. Python 把问题转换成向量，从 Qdrant 找到相关片段并返回。
7. Unity 将片段、问题和必要的聊天历史交给已配置的聊天模型。
8. Unity 显示回答与资料来源。没有足够资料时明确说明。

“从零”指自己编写文档处理、索引、检索、通信和问答流程；不从零训练 BGE-M3，不自己开发数据库，不复制旧项目业务代码。

### 1.1 固定职责，避免写重复代码

| 部分 | 负责什么 | 不承担什么 |
| --- | --- | --- |
| Python 后端 | 文档处理、生成向量、管理索引、返回检索片段 | 第一版不调用聊天模型，不提供 `/chat` |
| FastAPI | 接收 HTTP 请求、校验数据、返回结果 | 本身不具备检索能力 |
| Qdrant | 保存向量和原文元数据、执行相似度检索 | 不生成回答 |
| Unity C# | 输入与显示、调用检索接口、组织提示词、调用聊天模型 | 不运行 BGE-M3、不直接维护 Qdrant |
| 聊天模型 | 根据问题与资料生成回答 | 不负责给文档建立索引 |

Python 和 Unity 通过 `http://127.0.0.1:8000` 通信，可以都运行在同一台电脑。本地检索不等于全系统离线：若使用云端聊天模型，选中的资料片段和问题会发送给该模型。

### 1.2 第一版范围

- [ ] 单用户、单个知识库、本机 Windows 桌面客户端。
- [ ] TXT、Markdown、结构明确的 JSON。
- [ ] 手动导入、手动更新索引。
- [ ] BGE-M3 的 dense 向量检索。
- [ ] FastAPI 检索接口。
- [ ] Unity 文字问答、来源显示、取消、超时和错误状态。
- [ ] 基础检索评估与冷启动/热查询性能记录。

第一版暂不加入 PDF、Word、OCR、联网搜索、账号系统、重排序、语音输入、TTS 和数字人模型。它们在文字问答验收后逐项扩展。因为这是新项目，所以没有“关闭旧全文注入”和“清理旧会话快照”的迁移任务。

## 2. 软件、目录与运行约定

### 2.1 使用哪些软件

| 软件/组件 | 用途 |
| --- | --- |
| PyCharm | 编写和调试 Python 后端 |
| 已有 Python 3.11 虚拟环境 | 安装、隔离后端依赖 |
| Docker Desktop（Linux 容器） | 本地运行 Qdrant |
| Unity Hub / Unity Editor | 创建新的客户端项目、场景、UI，运行与打包 |
| Rider 或已有的 Visual Studio | 编写、调试 Unity C# |
| 浏览器 | 使用 FastAPI `/docs` 测试接口 |
| Git（可选但建议） | 记录每个阶段的代码变化 |

已检查：本目录已有 `.idea` 和 `.venv`，虚拟环境配置记录 Python 3.11.9。计划沿用它们，不删除、不重复创建。该记录不代表依赖已经安装，也不代表环境已通过运行验证。

PyCharm 直接打开整个 `Rag` 目录，解释器选择：

```text
C:\Users\qqcom\PycharmProjects\Rag\.venv\Scripts\python.exe
```

### 2.2 目标目录

```text
Rag/
├── plan.md
├── README.md
├── .gitignore
├── .venv/                         # 已有，保留，勿提交
├── backend/
│   ├── __init__.py
│   ├── .env                       # 本机配置，勿提交
│   ├── .env.example               # 空密钥配置示例
│   ├── requirements.txt           # 手动维护直接依赖
│   ├── requirements-lock.txt      # 验证成功后记录的版本
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py              # 配置与路径
│   │   ├── models.py              # 文档、片段、检索结果结构
│   │   ├── loader.py              # 文档读取
│   │   ├── splitter.py            # 分段
│   │   ├── embedding.py           # 本地模型加载、编码
│   │   ├── vector_store.py        # Qdrant 访问封装
│   │   ├── retriever.py           # 检索业务流程
│   │   ├── schemas.py             # HTTP 请求/响应结构
│   │   └── main.py                # FastAPI 启动与路由
│   ├── ingest.py                  # 命令行导入工具
│   ├── cli.py                     # 命令行检索工具，不生成回答
│   ├── scripts/
│   │   ├── __init__.py
│   │   ├── benchmark_embedding.py # 测本机模型速度
│   │   └── evaluate_retrieval.py  # 计算检索指标
│   └── tests/
│       ├── fixtures/              # 可公开的合成测试资料
│       ├── test_loader.py
│       ├── test_splitter.py
│       ├── test_retriever.py
│       └── test_api.py
├── data/
│   ├── documents/                # 真实知识文档，默认勿提交
│   ├── manifests/                # 每次导入的版本与统计
│   └── evaluations/              # 自建评估集、结果
├── models/                       # 下载缓存或本地模型，勿提交
├── logs/                         # 本地诊断日志，勿提交
├── infra/
│   ├── compose.yaml              # Qdrant 启动配置
│   ├── .env                      # 本机镜像版本配置
│   └── .env.example
├── docs/
│   ├── api-contract.md
│   ├── environment.md
│   ├── benchmark.md
│   └── evaluation.md
└── unity-client/                  # 最后由 Unity Hub 创建
    ├── Assets/
    │   ├── Scenes/Main.unity
    │   └── Scripts/
    │       ├── RagModels.cs
    │       ├── RagClient.cs
    │       ├── ChatModels.cs
    │       ├── ChatClient.cs
    │       ├── PromptBuilder.cs
    │       ├── ChatController.cs
    │       ├── ChatUI.cs
    │       └── RuntimeSettings.cs
    ├── Packages/
    └── ProjectSettings/
```

不要手工创建 Unity 的全部工程文件；到对应阶段通过 Unity Hub 生成。Qdrant 数据使用 Docker 命名卷，不把数据库内部存储当成普通代码文件。

### 2.3 命令约定

所有 PowerShell 示例都从项目根目录执行：

```powershell
Set-Location 'C:\Users\qqcom\PycharmProjects\Rag'
.\.venv\Scripts\python.exe --version
```

统一使用 `.\.venv\Scripts\python.exe -m ...`，不依赖虚拟环境是否已激活。PyCharm 运行配置也使用项目根目录作为 Working directory，并以 module name 方式运行 `backend.ingest` 等模块。

## 3. 阶段 0：确认环境、创建骨架

**目标：确保后面的代码能在同一套环境中运行。**

### 操作步骤

1. 在 PyCharm 打开 `Rag`，确认解释器指向已有 `.venv`。
2. 运行上面的 `--version`，确认虚拟环境可执行。
3. 创建 `backend`、`backend/app` 等 Python 目录和对应 `__init__.py`。
4. 创建 `data/documents`、`data/manifests`、`data/evaluations`、`models`、`logs`、`infra`、`docs`。
5. 创建 `.gitignore`，排除 `.venv/`、`.idea/`、`__pycache__/`、`.pytest_cache/`、真实 `.env`、`models/`、`logs/`、本地资料与导入产物，以及 Unity 的 `Library/`、`Temp/`、`Obj/`、`Logs/`、`UserSettings/`、构建目录。
6. `.env.example`、合成测试资料、Unity 的 `.meta`、`Assets`、`Packages`、`ProjectSettings` 应保留在版本控制中。
7. 在 `docs/environment.md` 记录 Python 版本、Windows 版本、CPU、内存、可用磁盘空间；如有 NVIDIA GPU，记录 GPU 和驱动信息。
8. 确认 Docker Desktop 可运行 Linux 容器。安装依赖、下载模型前先检查剩余空间；实际内存、显存占用以阶段 4 测试为准。

### 验证

```powershell
.\.venv\Scripts\python.exe -m pip --version
docker version
docker compose version
```

**完成标准：** Python 和 Docker 都能运行；新代码目录与原数字人项目完全独立。

## 4. 阶段 1：安装依赖，写配置读取

**涉及文件：** `backend/requirements.txt`、`backend/app/config.py`、`backend/.env.example`、`backend/.env`。

### 操作步骤

1. 按机器条件先安装 PyTorch：有兼容 NVIDIA 环境时选择官方匹配的 CUDA 构建；否则先用 CPU。不要仅根据“装过 CUDA”判断 PyTorch 能使用 GPU。
2. 从 [PyTorch 官方安装选择器](https://pytorch.org/get-started/locally/) 获取当前合适的命令，使用本项目解释器执行它。
3. 在 `requirements.txt` 维护直接依赖：`fastapi`、`uvicorn`、`qdrant-client`、`FlagEmbedding`、`pydantic-settings`、`pytest`、`httpx`。
4. 执行安装，检查是否有依赖冲突；不要在没有验证的情况下随意混用网上不同教程的版本。
5. `config.py` 明确根据自身文件路径求出项目根目录；不要根据终端当前目录猜资料位置。
6. 显式加载 `backend/.env`。Docker 的 `infra/.env` 由 Compose 使用，两者不自动互通。
7. 对整数、目录、URL、设备类型做校验；例如 `chunk_overlap` 必须小于 `chunk_size`。
8. 在导入 Hugging Face/模型相关库前设置模型缓存目录，确保模型不意外下载到另一处。

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print('CUDA available:', torch.cuda.is_available())"
```

### 建议的起始配置

以下是设计起点，不是适用于所有资料的最佳参数。路径相对于项目根目录解析。

| 字段 | 起始值 | 说明 |
| --- | --- | --- |
| `DOCUMENTS_DIR` | `data/documents` | 知识文档目录 |
| `HF_HOME` | `models/huggingface` | 下载缓存路径，代码转换为绝对路径 |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | 固定模型与后续记录的 revision |
| `EMBEDDING_DEVICE` | `cpu` | 先验证，再按实测改为 `cuda` |
| `EMBEDDING_FP16` | `false` | CPU 基线关闭；GPU 按兼容性实测 |
| `EMBEDDING_BATCH_SIZE` | `1` | 跑通后逐步增加 |
| `EMBEDDING_MAX_TOKENS` | `1024` | 编码 token 上限，不等于字符数 |
| `QDRANT_URL` | `http://127.0.0.1:6333` | 本机数据库 |
| `QDRANT_ALIAS` | `rag_active` | 查询使用的稳定索引别名 |
| `CHUNK_SIZE` | `600` | 字符数软上限 |
| `CHUNK_OVERLAP` | `100` | 连续片段重叠字符数 |
| `RETRIEVAL_TOP_K` | `5` | 返回片段数量上限 |
| `MAX_CONTEXT_CHARS` | `4000` | 片段正文总字符预算 |
| `MIN_SCORE` | 不设，待标定 | 不能把相似度当成正确率 |

**完成标准：** 配置能正确打印非敏感摘要；模型目录、文档目录在 PyCharm 与终端中解析一致；缺少必要配置时能明确报错。

## 5. 阶段 2：读取文档并保留来源

**涉及文件：** `models.py`、`loader.py`、`test_loader.py`。

### 先准备少量资料

创建 3～5 份合成资料，例如图书馆时间、食堂地点、办卡流程。明确标注这些是测试数据，不冒充实际校园信息。先用 10～30 个片段规模调通链路。

### 数据结构

`Document` 至少包含：

- `document_id`：从相对路径生成的稳定标识。
- `source`：相对于 `data/documents` 的路径，保留子目录，避免同名文件混淆。
- `title`：文件或业务条目标题。
- `text`：用于检索的正文。
- `section`：章节标题，可空。
- `record_key`：JSON 业务条目标识，可空。
- `content_hash`：内容摘要，用于辨别版本。

### 操作步骤

1. 定义上述数据结构，再写 `load_documents(root)`。
2. 递归查找 `.txt`、`.md`、`.json`，排序以保持输出稳定。
3. 使用 UTF-8/UTF-8 BOM 兼容读取；编码失败时说明文件名，不用忽略错误的方式吞字。
4. TXT 保留段落；Markdown 保留标题层级和标题所属正文，不要简单删掉全部符号后把结构打散。
5. JSON 第一版约定两种输入：单条对象，或对象数组。字段例如 `id`、`title`、`content`；业务字段如地点、时间、电话应转换成带字段名的可读文本。
6. 一个 JSON 数组条目先作为一个独立文档单元，不把不同楼宇或不同业务条目混在一起。
7. 对不符合约定的 JSON 给出错误位置和预期格式，不承诺任意 JSON 都能自动得到合理语义。
8. 区分“空文件”“格式不支持”“解析失败”，统计成功和失败数量。
9. 返回文档集合和诊断信息，由导入工具决定能否继续；不要在加载器里操作数据库。

### 验证

- [ ] 中文、BOM、空文件、损坏 JSON 有明确行为。
- [ ] 两个子目录里的同名文件有不同来源标识。
- [ ] JSON 数组的两个条目不会混为一段。
- [ ] 非支持格式被跳过并有统计。

**完成标准：** 可以打印每份资料的标题、来源、正文长度，并抽查内容与原文一致。

## 6. 阶段 3：按语义边界分段

**涉及文件：** `splitter.py`、`test_splitter.py`。

### 片段结构

`Chunk` 至少包含 `chunk_id`、`document_id`、`source`、`title`、`section`、`record_key`、`chunk_index`、`text`、`content_hash`。

`chunk_id` 建议由来源、条目标识、内容版本、段落位置生成确定性的 UUID。Qdrant point ID 使用其支持的 UUID 或整数，不直接把任意十六进制哈希当成 point ID。

### 操作步骤

1. 写 `split_document(document, chunk_size, overlap)`。
2. 优先在章节和空行处分组，过长段落再按句号、问号、分号等边界拆分。
3. 初始采用约 600 字符一段、100 字符重叠；重叠仅发生在同一文档单元中。
4. 长度超过限制而没有标点时，使用可前进的硬切分兜底，防止死循环。
5. 不跨 JSON 业务条目做重叠；名称、地址、时间、电话尽量放在同一业务片段中。
6. 片段携带章节标题；可用“标题 + 正文”做 embedding 输入，但保留原始正文用于显示与引用。
7. 在使用 tokenizer 后再校验 token 长度；确实超限的段落继续分割，不能静默截断重要尾部。
8. 将结果输出成检查表或临时 JSONL，人工查看 10 个片段。

### 验证

- [ ] 没有空片段，算法终止，顺序稳定。
- [ ] 合并考虑重叠后，原文有效内容没有丢失。
- [ ] 片段有来源、标题，业务名称与答案没有无意分离。
- [ ] 长段落、无标点文本、边界长度都有覆盖。
- [ ] 相同输入和参数重复执行得到相同片段标识。

**完成标准：** 已知问题的答案能在某个片段中完整找到。不要仅根据片段长度合格就认为分段效果合格。

## 7. 阶段 4：加载 BGE-M3，测试本机性能

**涉及文件：** `embedding.py`、`scripts/benchmark_embedding.py`、`docs/benchmark.md`。

### 操作步骤

1. 使用 `FlagEmbedding` 的 `BGEM3FlagModel` 封装一个 `EmbeddingService`，对外提供 `encode_documents(texts)` 和 `encode_query(query)`。
2. 每个进程只加载一次模型。第一次运行需要下载，下载耗时与每次问答耗时分开记录。
3. 第一版仅使用 dense 输出。BGE-M3 的 dense 向量维度为 1024；编码后仍通过实际结果验证维度和数值是否有效。参考 [模型官方说明](https://huggingface.co/BAAI/bge-m3)。
4. 文档和问题使用同一模型、revision、编码配置以及一致的归一化策略。
5. CPU 先用 FP32、小 batch；GPU 可在确认兼容后尝试 FP16。参数必须以实际测量为准。
6. 记录加载时间、单问题预热后延迟、批量文档编码速度、内存/显存峰值，以及输入 token 长度。
7. 分别用准确表达、同义表达、完全不相关表达进行相似度比较。
8. 用真实中文样本连续测至少 30 次热查询，记录 p50、p95，不用一次最快结果代表性能。
9. 将模型版本与编码参数写入索引指纹；之后更换模型或编码策略时重建文档向量，即使维度相同也不能混用。

### 先设一个可修改的性能目标

可把“热检索全过程 p95 不超过 2 秒”作为交互体验目标，但它不是 BGE-M3 在当前机器上的保证。如果未达标：先查模型重复加载、设备选择、输入长度和 batch，再考虑 GPU 或更小中文向量模型。换模型后重新评估、重建索引。

**完成标准：** 能生成有效向量；同义问题能接近正确片段；机器能够稳定运行，且已记录实际速度。

## 8. 阶段 5：部署 Qdrant，封装数据库操作

**涉及文件：** `infra/compose.yaml`、`infra/.env`、`vector_store.py`。

### 操作步骤

1. 根据 [Qdrant 官方本地部署说明](https://qdrant.tech/documentation/quick-start/) 选择并记录一个明确的镜像版本，写入 `infra/.env` 的 `QDRANT_IMAGE`，格式为 `qdrant/qdrant:<实际选择的版本标签>`。
2. `compose.yaml` 使用 `${QDRANT_IMAGE:?set QDRANT_IMAGE in infra/.env}`，让未配置版本时直接报错；版本占位符不能原样运行。
3. 端口映射为 `127.0.0.1:6333:6333`，第一版使用 HTTP，不必额外开放 gRPC 端口。
4. 使用命名卷 `rag_qdrant_data`，挂载到容器 `/qdrant/storage`；明确设置卷名以免更换 Compose 目录后误以为数据丢失。
5. Windows 上采用命名卷，避免直接把数据库存储挂载到普通 Windows 项目目录。参考 [Qdrant 存储与安装说明](https://qdrant.tech/documentation/installation/)。
6. 写 `VectorStore`：连接检查、创建 collection、批量 upsert、查询、检查统计信息、切换 alias。
7. collection 使用与模型一致的维度及 Cosine 距离。先用几条人为构造的同维向量验证数据库操作，再接模型。
8. 查询使用所锁定客户端版本支持的 Query API（例如 `query_points`），不要直接照搬旧教程里已变化的方法签名。
9. collection 名使用版本，如 `rag_v001`；查询统一走 alias `rag_active`。版本切换在阶段 6 实现。

```powershell
docker compose --env-file infra/.env -f infra/compose.yaml up -d
docker compose --env-file infra/.env -f infra/compose.yaml ps
Invoke-RestMethod -Uri 'http://127.0.0.1:6333/collections'
```

### 要存储的 payload

保存 `chunk_id`、`document_id`、`source`、`title`、`section`、`record_key`、`chunk_index`、`text`、`content_hash`、`index_version`。不只存向量，否则检索到结果后没有原文可交给聊天模型。

### 验证

- [ ] 写入几条测试 point 后能够查询并返回 payload。
- [ ] 重启容器后数据仍然存在。
- [ ] 数据库不可用时报告连接异常，不伪装成“没有相关资料”。
- [ ] 维度不匹配时立即报错。

**完成标准：** 本机数据库能持久化数据，Python 封装能稳定读写。

## 9. 阶段 6：完成文档导入与可重复更新

**涉及文件：** `ingest.py`、`data/manifests`。

### 第一版采用“新 collection 重建后切换”

先不做复杂的逐文件增量更新。资料量小时全量重建更容易理解，也能正确处理删除和修改，避免旧片段继续出现在结果里。

### 操作步骤

1. 实现 `python -m backend.ingest --dry-run`：扫描、解析、分段、报告数量，不调用模型、不修改数据库。
2. 明确本次导入的文档根目录、支持格式、失败文件、片段数。存在解析错误时默认中止发布新索引，保留旧索引。
3. 实现 `--rebuild`：为本次导入创建新的 collection，按批生成向量并 upsert。
4. 写 manifest：文件内容哈希、分段参数、模型 revision、编码参数、向量维度、collection、文档和片段数量、构建时间。
5. 校验写入数量和抽样检索结果。空目录默认不切换到空知识库，提示用户检查路径。
6. 新索引完整后，用 Qdrant alias 更新操作将 `rag_active` 切换到它；旧 collection 保留以便回滚。
7. 任何批次失败都不得切换 alias。失败构建可以下次清理，但不要删除仍在使用的版本。
8. 第一版不允许同时执行两次导入；加导入锁并给出明确提示。
9. 为降低本机模型内存压力，首版操作约定为：停止 FastAPI → 导入 → 重启 FastAPI。避免导入和服务各加载一份 BGE-M3 耗尽内存。
10. 更新、删除资料后执行同一重建命令，并验证修改生效、删除内容消失。

```powershell
.\.venv\Scripts\python.exe -m backend.ingest --dry-run
.\.venv\Scripts\python.exe -m backend.ingest --rebuild
```

### 验证

- [ ] 同一批资料重建两次，活动索引内片段数量稳定，不翻倍。
- [ ] 文档从“22:00”改成“21:00”后，活动索引只包含新版本。
- [ ] 删除文档并重建后，不再返回其旧片段。
- [ ] 模拟中途失败，旧 alias 仍然可查询。

**完成标准：** 知识库可创建、更新、删除内容，并且失败导入不会破坏现有可用索引。

## 10. 阶段 7：先做命令行检索，再写 HTTP

**涉及文件：** `retriever.py`、`cli.py`、`test_retriever.py`。

### 操作步骤

1. 定义 `retrieve(query, top_k)`，输入问题，输出结构化结果，不生成回答。
2. 拒绝空白问题，限制过长输入，默认 `top_k=5`，允许范围暂定 1～10。
3. 使用已加载模型编码问题，查询 `rag_active`，要求返回 payload，正常情况下不返回完整向量。
4. 检索候选可以多取一些，再去重、按预算保留至多 top_k 段，避免大半上下文都是相邻重复文字。
5. 按总字符预算选取片段，保留来源与编号。不要截断 JSON 返回值；正文需裁剪时明确标记。
6. 记录编码、数据库查询、总耗时，以便定位速度问题。
7. 返回 `status=ok` 或 `status=no_match`。后端失败抛明确异常，不能返回空数组掩盖失败。
8. 初期不过滤分数，用评估集观察分布；阶段 12 再标定阈值。top_k 总能找到“相对最近”的结果，不代表知识库确实含答案。
9. CLI 支持单次参数和循环输入两种模式；循环模式复用模型，避免每输入一个问题就重新加载。

```powershell
.\.venv\Scripts\python.exe -m backend.cli --query '晚上九点还能去图书馆吗？'
.\.venv\Scripts\python.exe -m backend.cli --interactive
```

### 验证

直接问、同义改写、精确地点或编号、完全无关的问题分别执行。人工检查检索原文是否包含答案，不能只看分数高低。

**完成标准：** 正确资料能进入前 5 段；耗时可见；错误可辨别。此时得到的是检索系统，还需要聊天模型才能完成问答式 RAG。

## 11. 阶段 8：用 FastAPI 提供检索接口

**涉及文件：** `schemas.py`、`main.py`、`docs/api-contract.md`、`test_api.py`。

### 操作步骤

1. 先把接口约定写入 `api-contract.md`，再实现 Python schema 和路由。
2. 使用应用 lifespan 初始化配置、模型和数据库连接，关闭时释放资源。
3. 开发初期只开一个 worker；多个 worker 会各自加载一份模型。重型模型调试阶段先不启用自动 reload。
4. `GET /health` 用于进程存活检查；`GET /ready` 检查模型、Qdrant、活动索引与指纹，未就绪返回 503。
5. 实现 `POST /retrieve`；验证 query、top_k，调用已经通过 CLI 验证的检索函数，不在路由中重写算法。
6. 模型推理是阻塞工作：使用同步 `def` 路由在线程池执行，或明确卸载阻塞部分。仅把函数改成 `async def` 不能解决阻塞。参考 [FastAPI 并发说明](https://fastapi.tiangolo.com/async/)。
7. 单模型设置有界并发，第一版可限制一次推理；繁忙请求明确返回 429/503 或受限排队，避免无限堆积。
8. 请求分配 request_id，用于将 Unity 错误与服务日志关联；日志默认不记录完整私人文档、密钥和提示词。
9. 对无匹配返回 200 和 `no_match`；参数非法 422；依赖不可用 503；意外失败 500。错误 JSON 也使用稳定结构。

### 请求约定

```json
{
  "query": "图书馆晚上几点关门？",
  "top_k": 5
}
```

### 成功响应示例（内容、分数、耗时均为演示）

```json
{
  "request_id": "request-example",
  "status": "ok",
  "query": "图书馆晚上几点关门？",
  "index_version": "rag_v001",
  "results": [
    {
      "chunk_id": "cc40735b-a601-4f62-8868-40a3ed35d937",
      "source": "campus/library.md",
      "title": "图书馆使用说明（合成测试资料）",
      "section": "开放时间",
      "text": "图书馆开放时间为每日 8:00—22:00。",
      "score": 0.78
    }
  ],
  "timings_ms": {
    "embedding": 150,
    "search": 12,
    "total": 170
  }
}
```

无匹配时同样保留 request_id、query、index_version、timings_ms，`status` 为 `no_match`，`results` 为空数组。错误响应约定为 `request_id`、`error.code`、`error.message`；参数校验异常也做相应适配，保持客户端易于处理。

### 启动与验证

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

新开一个终端或浏览器检查：

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/ready`
- `http://127.0.0.1:8000/docs`

在 `/docs` 提交 query 并与 CLI 结果比较。测试空字符串、非法 top_k、Qdrant 停止、活动索引不存在等情况。

**完成标准：** 不打开 Unity 也能通过 HTTP 获得正确片段。模型每个进程只初始化一次，普通查询不访问文档源目录。

## 12. 阶段 9：创建新的 Unity 客户端和基础 UI

**涉及文件：** 新 Unity 工程、`Main.unity`、`ChatUI.cs`、`RuntimeSettings.cs`。

### 操作步骤

1. 用 Unity Hub 在 `Rag/unity-client` 创建一个新项目，记录编辑器版本。可沿用电脑上已有且可用的版本，无需为了 RAG 升级旧项目。
2. 第一版使用普通桌面项目，只做文字界面；不以 WebGL 为目标，避免引入另一套网络限制。
3. 保存场景为 `Assets/Scenes/Main.unity`。
4. 创建 Canvas 和 EventSystem，使用 TextMeshPro 输入框、发送按钮、取消按钮、回答区域、来源区域、状态栏。
5. 确保字体包含中文字符，先显示一段中文测试。
6. 创建 `ChatRoot` GameObject，后面挂载控制器。UI 字段通过 Inspector 绑定，缺少绑定时明确报错。
7. 先用本地假数据跑通 UI：点击发送显示输入，点击取消更新状态，暂不联网。
8. `RuntimeSettings` 设计检索地址、top_k、超时、聊天模型端点与模型名。真实聊天 API Key 从本机环境变量或本机私有配置读取，不写进场景和提交的资产。
9. 开发环境可由 Unity 直接调用聊天 API；未来要分发客户端时，将模型调用与服务端密钥迁到后端，避免把共享密钥打包出去。此项不要求现在改变第一版分工。

**完成标准：** Unity Play 模式能接受中文输入、显示文本、切换按钮与状态，没有脚本编译错误。

## 13. 阶段 10：实现 Unity 检索客户端

**涉及文件：** `RagModels.cs`、`RagClient.cs`。

### 操作步骤

1. 按 `api-contract.md` 定义 C# 请求、响应和错误 DTO，字段名与 JSON 完全一致。
2. 选择项目支持的 JSON 序列化方案。不要手工拼接 JSON 字符串，避免引号、换行和中文转义错误。
3. 用 `UnityWebRequest` POST UTF-8 JSON，设置 `Content-Type: application/json`，返回体用下载处理器读取。
4. 封装异步 `RetrieveAsync(query, topK, cancellationToken)`，内部通过与 Unity 主线程兼容的更新/协程桥接处理完成事件。
5. 所有 UnityWebRequest 操作及 UI 修改遵守 Unity 线程约束；不要在后台取消回调中直接访问 Unity 对象。
6. 设置可配置的请求超时，按阶段 4 的机器实测调整。区分超时、用户取消、连接失败、HTTP 错误、JSON 解析错误。
7. 用户取消时在 Unity 主线程调用 Abort，完成后释放请求资源，避免重复触发结果回调。
8. 服务端已经开始的 CPU/GPU 推理不一定能因 HTTP 断开立即停止；客户端必须丢弃过期结果，不能依赖服务器瞬间取消。
9. 暂时把检索原文和来源显示在 UI，确认 Python/Unity 的字段和中文编码一致后再调用聊天模型。

参考：[UnityWebRequest 官方文档](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/Networking.UnityWebRequest.html)。

### 验证

- [ ] 正常问题返回与 `/docs` 相同的片段。
- [ ] 停止 Python 服务后显示“检索服务不可用”。
- [ ] 取消后不再显示该次结果。
- [ ] 服务器错误不被显示成“知识库没有答案”。
- [ ] 引号、换行、中文问题不会损坏请求 JSON。

**完成标准：** Unity 能可靠调用本地检索服务，失败和取消行为清晰。

## 14. 阶段 11：接入聊天模型，完成真正的 RAG 回答

**涉及文件：** `ChatModels.cs`、`ChatClient.cs`、`PromptBuilder.cs`、`ChatController.cs`。

### 14.1 先单独实现聊天 API 调用

1. 明确当前要用的模型提供方、真实 API 地址、模型名、鉴权方式，并查该提供方当前官方文档；不假设所有服务完全兼容。
2. `ChatClient` 先实现非流式调用，发送一个简单问题，验证鉴权和回答解析。
3. 不在 Python 后端再写一套回答生成器；本阶段回答生成统一在 Unity 客户端编排。
4. 检索和聊天调用使用同一轮 CancellationToken；异常时保留用户输入，让用户可以重试。

### 14.2 组织提示词

1. `PromptBuilder` 输入：当前问题、选中的检索片段、受长度限制的最近历史。
2. 每段分配 `[S1]`、`[S2]` 等本轮来源编号，并记录编号到真实 chunk_id/source 的映射。
3. 明确告诉模型：资料是参考数据，不是指令；回答仅依据资料；资料不支持的部分要说明，不补造时间、电话、规定。
4. 把可信规则与资料放在明确分隔的消息/数据区域，避免将原文里的“忽略之前指令”等内容当成规则执行。
5. 明确要求事实回答引用本轮来源编号。UI 中的文件来源必须来自检索结果，不使用模型自行编造的路径或 URL。
6. 验证模型输出的引用编号是否存在。未知编号不得变成可点击来源；编号存在也不等于事实已验证，仍需评估答案与原文是否一致。
7. 每轮重新检索，不把上一轮的检索全文永久加入聊天历史。历史只保存必要的用户/助手消息，来源可作为独立元数据保存。
8. 初始只保留最近少量轮次；总提示词仍需满足所选模型的 token 预算，4000 字符并不等于 4000 tokens。

### 14.3 定义控制器状态与顺序

```text
空闲 → 检索中 → 生成回答中 → 完成
                ↘ 无资料 → 提示资料不足
任一进行中状态 → 取消 / 失败 → 恢复可输入
```

1. 每轮创建递增的 turn_id 和取消源，生成前检查输入。
2. 调用 `RagClient`。
3. `no_match` 时直接说明知识库未找到相关资料，不强制让模型猜答案。
4. 检索异常时停止本轮并报告服务错误，不静默变成无依据聊天。
5. 有片段则通过 `PromptBuilder` 组装上下文，交给 `ChatClient`。
6. 有返回片段但不能支持答案时，依赖模型的资料约束与后续评估；不能把 `status=ok` 解释成“必定有答案”。
7. 显示答案和有效来源列表，只把最终本轮有效结果加入历史。
8. 新一轮开始或取消后，迟到的检索、聊天回调必须检查 turn_id，防止旧结果覆盖新问题。
9. 完成后进入统一的 finally 清理，恢复按钮状态；取消不显示成系统故障。

### 14.4 处理连续追问

先完成独立问题，再支持“它周末开门吗”一类问题：

1. 保留最近的用户问题和已确认实体。
2. 生成明确的 retrieval_query，例如“图书馆周末开放时间”；原始用户问题仍用于最终回答。
3. 第一版先用简单的显式主题继承；不能确认指代时请用户说清对象。
4. 后续可通过聊天模型生成独立检索问题，但应限制历史长度，验证输出，并记录额外耗时。
5. 分别记录原始 query 与 retrieval_query，便于查明错误来自改写还是检索。

**完成标准：** 用户在新 Unity 项目中提问，能看到有依据的回答和可追溯来源；知识不足和服务失败明确区分。

## 15. 阶段 12：评估准确性、标定阈值

**涉及文件：** `data/evaluations/questions.jsonl`、`evaluate_retrieval.py`、`docs/evaluation.md`。

### 建立 20～30 条小型评估集

每条记录：问题、预期相关 source/record_key、答案关键事实、是否应无答案、问题类别。不要只绑定会随重建改变的 chunk_id。可按以下类别分配：

| 类别 | 示例 | 检查点 |
| --- | --- | --- |
| 直接提问 | 图书馆几点关门 | 基础检索是否命中 |
| 同义表达 | 晚上九点还能去看书吗 | 语义匹配 |
| 精确实体 | 某楼的服务电话 | 实体与编号是否混淆 |
| 多项事实 | 办卡地点和材料 | 是否需要多个片段 |
| 无答案 | 文档没有记载的问题 | 是否拒绝编造 |
| 连续追问 | 它周日也开吗 | 指代是否正确 |
| 资料含指令 | 资料中夹有“忽略规则” | 是否仍把它当数据 |

### 操作步骤

1. 人工建立正确答案依据，避免让系统自己给自己的结果打标签。
2. 计算可回答问题的 Hit@5：前 5 段是否至少出现一个相关依据；多依据问题另记证据覆盖率。
3. 单独检查最终事实正确率、来源真实性、无答案误答率，检索命中不等于回答正确。
4. 分析相关与无关片段分数分布，用部分样例调阈值，再用未参与调参的样例验收。样本少时如实标注局限。
5. 不把 0.7 或其他固定相似度解释成“70% 正确”。阈值必须与模型、语料、编码和距离度量对应。
6. 每次只调整一种因素，例如分段、top_k 或阈值，保留前后结果，避免不知道是哪项产生影响。
7. 对楼名、编号匹配不足的情况，先修文档和切分；仍不足时，在后续阶段增加关键词/稀疏检索。

### 第一版建议验收目标

- [ ] 小型测试集中，可回答问题 Hit@5 达到约 90% 或以上，逐条记录未命中的原因。
- [ ] 测试集关键事实与原文一致；模型没有编造来源编号。
- [ ] 无答案样例能给出资料不足提示；实际观察到的失败如实记录，不以提示词保证零幻觉。
- [ ] 更新、删除、重启、取消、离线错误场景均可重现且行为符合设计。
- [ ] 热检索 p50/p95、聊天首响应/总耗时分别可查，不承诺未测量的端到端速度。

以上是项目自定的初始验收目标，不是系统已达到的结果。20～30 条样例只能作为学习版质量检查。

## 16. 阶段 13：锁定环境、整理启动与停机步骤

**涉及文件：** `README.md`、`requirements-lock.txt`、`docs/environment.md`。

### 操作步骤

1. 基础流程验证后再记录 Python 依赖版本、PyTorch 构建来源、模型 revision、Qdrant 镜像版本和 Unity 版本。
2. 可用 `pip freeze` 写入锁定文件，但同时记录 PyTorch 的安装索引/硬件条件；仅有 freeze 文件不一定能在另一台不同 GPU 的电脑上复现。
3. 整理 `.env.example`，确保没有真实密钥、私人绝对路径或文档正文。
4. 写 README：项目用途、安装顺序、配置、导入命令、运行命令、已知限制、故障排查。
5. 打包一个 Windows 客户端做一次完整验证。先不增加自动启动 Docker/Python 的功能，避免把环境管理与 RAG 混在一起。

### 日常启动顺序

1. 启动 Docker Desktop。
2. 启动 Qdrant：

```powershell
docker compose --env-file infra/.env -f infra/compose.yaml up -d
```

3. 首次导入或资料变化时，停止 FastAPI 后运行：

```powershell
.\.venv\Scripts\python.exe -m backend.ingest --dry-run
.\.venv\Scripts\python.exe -m backend.ingest --rebuild
```

4. 启动 FastAPI：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

5. `/ready` 返回就绪后，再在 Unity 中进入 Play 模式或运行打包客户端。

### 停机

退出 Unity，终端 Ctrl+C 结束 FastAPI，再按需要执行：

```powershell
docker compose --env-file infra/.env -f infra/compose.yaml stop
```

日常停机不要使用删除卷的命令。Qdrant 索引可以由源文档重建，但知识文档本身需要单独备份。

**完成标准：** 按 README 从服务停止状态启动，可以完成一次问答；模型和索引无需每次重新下载或重建。

## 17. 后续增强顺序

基础版验收后，每次只加一个功能，并复用已有评估集检查是否退步。

1. **流式回答**：在 ChatClient 中处理所选模型的流协议。若为 SSE，处理跨网络包的 UTF-8、事件边界、结束标记和取消；不能把单次下载块当成完整事件。
2. **混合检索**：加入关键词/BM25 或稀疏向量召回，用可解释的融合策略，再评估实体与编号问题。
3. **重排序**：候选较多时引入 reranker；同时测准确率收益与额外延迟。
4. **PDF/Word**：新增 loader，保留页码、标题、表格关系；扫描 PDF 另加 OCR，不把空文本识别成成功导入。
5. **增量索引**：按内容哈希跳过未改文件，正确删除旧版本片段，保留失败回滚能力。
6. **语音与数字人**：语音识别产生文字后进入同一 ChatController；最终回答文本进入 TTS 和口型流程；引用元数据用于 UI，不直接朗读。
7. **打包与远程使用**：如需分发应用，再引入后端聊天代理、鉴权、加密传输、服务部署。多设备时 `127.0.0.1` 指各设备自身，需要重新规划地址，不能直接沿用本机配置。

## 18. 常见问题排查表

| 现象 | 优先检查 |
| --- | --- |
| Python 找不到模块 | 是否使用根目录 `.venv`；是否以 `-m backend...` 运行 |
| 模型总是重新下载 | HF_HOME 是否在加载库前设置；不同运行方式是否用了不同缓存 |
| 每个问题都很慢 | 是否重复加载模型；CPU/GPU 实际选择；输入 token 长度 |
| 内存或显存不足 | 是否同时运行导入和 API；是否开多 worker；batch 是否过大 |
| 无法连接 Qdrant | Docker 是否运行；6333 是否映射；QDRANT_URL 是否正确 |
| 容器重启后像是丢数据 | 是否使用同一个命名卷；是否误删卷或改了卷名 |
| 查到删除过的内容 | alias 是否指向新版本；是否只 upsert 却从未删除旧片段 |
| 文档存在但查不到 | loader 是否成功；分段是否合理；索引是否更新；编码指纹是否一致 |
| 分数高但答非所问 | 分数不是答案概率；检查实体、分段和无答案策略 |
| Unity 中文乱码 | UTF-8 编码、JSON 序列化、字体字符覆盖 |
| 取消后旧答案又出现 | turn_id 检查是否覆盖所有异步回调；UI 是否被旧任务更新 |
| 回答来源不存在 | 来源是否来自检索 DTO；是否信任了模型生成的路径 |
| 模型编造知识库外信息 | 原文是否真的含答案；提示词边界、历史污染、证据判断 |
| PyCharm 正常，终端失败 | 解释器、工作目录、env 文件路径是否一致 |

## 19. 里程碑与手写顺序

| 里程碑 | 对应阶段 | 你应该能演示什么 |
| --- | --- | --- |
| M1：读得懂资料 | 0～3 | 输出带来源的合理片段 |
| M2：本地找得到资料 | 4～7 | 终端提问，返回正确原文与分数 |
| M3：接口可调用 | 8 | 浏览器 `/docs` 返回检索结果 |
| M4：Unity 能检索 | 9～10 | Unity 输入问题，看到相关原文 |
| M5：完整 RAG | 11 | Unity 显示依据知识库生成的回答与来源 |
| M6：结果可验证、可复现 | 12～13 | 评估报告、更新验证、完整启动说明 |

建议按以下文件顺序动手，不要先写大型总控制器：

```text
config.py → models.py → loader.py → splitter.py
→ embedding.py → vector_store.py → ingest.py
→ retriever.py → cli.py → schemas.py → main.py
→ Unity UI → RagModels.cs → RagClient.cs
→ ChatModels.cs → ChatClient.cs → PromptBuilder.cs
→ ChatController.cs → 评估与启动文档
```

代码量只是参考：极简演示通常少于完整学习版；把本文的版本化索引、错误处理、取消和验证都写齐，约需 1,000～2,000 行 Python/C# 与测试配置，实际取决于实现方式。不要为了凑行数跳过验证，也不要把早期 400～700 行的极简估算理解为本文全部功能的上限。

## 20. 最终验收清单

- [ ] 所有业务代码属于这个新项目，没有修改原数字人项目。
- [ ] TXT、Markdown、JSON 能正确读取，来源和内容不混淆。
- [ ] 文档分段能保留完整业务信息。
- [ ] BGE-M3 在本机完成实际性能测试。
- [ ] Qdrant 持久化正常，模型与索引维度、指纹一致。
- [ ] 导入支持重复执行、更新、删除和失败保留旧索引。
- [ ] CLI 与 FastAPI 检索结果一致。
- [ ] Unity 能完成检索、调用聊天模型、显示回答和来源。
- [ ] 每轮只使用本轮资料，历史不积累知识库全文。
- [ ] 无答案、服务异常、取消、超时都有明确行为。
- [ ] 连续追问不会在不明确指代时凭空决定主题。
- [ ] 评估结果和延迟都有记录，未通过的样例列入已知问题。
- [ ] 文档、密钥、模型缓存没有被误提交。
- [ ] 完整重启后按 README 能恢复问答。

## 21. 官方参考资料

以下链接用于核对实际 API 与当前安装方式。开发时锁定自己的依赖版本；本计划中的业务结构和参数是本项目设计，不是官方默认值。

- [FastAPI 入门](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [FastAPI 并发与阻塞说明](https://fastapi.tiangolo.com/async/)
- [Qdrant 本地部署](https://qdrant.tech/documentation/quick-start/)
- [Qdrant 安装和存储说明](https://qdrant.tech/documentation/installation/)
- [Qdrant 文档入口：核对 collection、alias、Query API](https://qdrant.tech/documentation/)
- [BGE-M3 模型与编码示例](https://huggingface.co/BAAI/bge-m3)
- [PyTorch 安装选择器](https://pytorch.org/get-started/locally/)
- [UnityWebRequest 文档](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/Networking.UnityWebRequest.html)
- [Rider 的 Unity 支持](https://www.jetbrains.com/help/rider/Unity.html)
