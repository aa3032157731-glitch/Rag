# 从零手敲 RAG：阅读导航

这套教程落实上一级的 [plan.md](../plan.md)。目标是你亲手完成一个独立项目：Python 在本机检索知识文档，Unity 显示问题、回答和来源，DeepSeek 根据检索资料组织答案。

**先读 step01，再按编号前进。教程中的源码需要由你创建，Markdown 本身不会安装依赖或运行服务。**

## 一、怎么使用这些文档

1. 项目根目录固定为 `C:\Users\qqcom\PycharmProjects\Rag`。
2. 文件标题里的相对路径都相对于这个根目录。例如 `backend/app/config.py` 是 `C:\Users\qqcom\PycharmProjects\Rag\backend\app\config.py`。
3. “新建”表示先在 PyCharm/Unity 中创建文件，然后手敲整个代码块。不要把 Markdown 的三个反引号写进源文件。
4. “完整替换”表示删除该文件原有内容，再填写整块新代码；不要保留两个同名类或两个入口。
5. 标记为 PowerShell 的代码在 PyCharm Terminal 中执行，不写进 `.py` 文件。
6. 示例输出用于对照结构。向量分数、UUID、下载耗时和模型回答可能不同，不能要求逐字相等。
7. 每篇结尾有验收清单；检查通过再继续。遇到错误先读该篇排查表。
8. `<!-- file: ... -->` 是文档内部的代码验证标记，正常阅读时不可见，不用手敲。

## 二、学习路线

| 篇目 | 动手结果 | 是否需要外部服务 |
| --- | --- | --- |
| [step01 环境与配置](step01.md) | 能打印正确配置 | 安装依赖需网络 |
| [step02 文档加载](step02.md) | 能读取 3 种格式与来源 | 不需要 |
| [step03 文档分段](step03.md) | 能生成完整、稳定的片段 | 不需要 |
| [step04 向量模型](step04.md) | 能用 BGE-M3 编码并测速度 | 首次下载需网络 |
| [step05 Qdrant](step05.md) | 能持久化、写入和查询向量 | 本机 Docker |
| [step06 导入知识库](step06.md) | 能更新资料并切换索引 | 本机 Qdrant |
| [step07 命令行检索](step07.md) | 能询问并查看相关原文 | 本机 Qdrant |
| [step08 FastAPI](step08.md) | 能通过 HTTP 检索 | 本机 Qdrant |
| [step09 Unity 界面](step09.md) | 能输入并显示中文 | Unity Editor |
| [step10 Unity 检索](step10.md) | 能在 Unity 查看片段 | 本机 FastAPI |
| [step11 DeepSeek 问答](step11.md) | 能生成有来源的回答 | 本机服务 + DeepSeek |
| [step12 评估与交付](step12.md) | 能评估、更新、重启和打包 | 按测试场景启动 |

里程碑：step03 是文档处理；step07 是检索；step10 是客户端接通；step11 才是完整的问答式 RAG。

## 三、你正在搭建什么

```text
导入：文档 → loader → splitter → BGE-M3 → Qdrant

问答：Unity 输入 → HTTP /retrieve → BGE-M3 + Qdrant
                    ↓ 返回原文、来源、分数
      Unity 组装资料与问题 → DeepSeek → 显示回答和引用
```

| 术语 | 在本项目中的意思 |
| --- | --- |
| RAG | 先找参考资料，再让聊天模型据此回答 |
| chunk | 从文档拆出的一段可独立使用的文字 |
| embedding | 用向量表示文字含义的过程 |
| dense vector | 一组固定长度数字；这里用于相似度比较 |
| collection | Qdrant 内的一组向量与元数据 |
| payload | 和向量一起存储的原文、标题、来源 |
| alias | 指向某个 collection 的稳定名字，本项目为 `rag_active` |
| top_k | 最多返回多少段，不是保证有多少条正确答案 |
| HTTP API | 程序间的请求与响应约定，不等于云端服务 |
| DTO | C# 中与 JSON 对应的数据类 |

不实现联网搜索。文档、索引与 BGE-M3 在本机；DeepSeek 是云端服务，会收到你选中的原文片段和对话内容。测试先使用教程提供的合成资料。

## 四、操作与版本约定

- Python：使用已有 `.venv`，版本记录为 3.11.9；不重建该目录。
- PowerShell：每个新终端先执行 `Set-Location 'C:\Users\qqcom\PycharmProjects\Rag'`。
- PyCharm Run Configuration：选择 **Module name**，例如 `backend.ingest`；Working directory 为根目录；不要以裸脚本路径启动带包导入的文件。
- 初期先用 CPU、单模型进程。机器有 4GB 显存的 RTX 3050 Laptop，但教程不假定 GPU 环境已经可用。
- Unity：2022.3.62f3，3D Core 桌面项目；C# 使用 Newtonsoft JSON、TextMeshPro、UnityWebRequest。
- 模型下载与真实问答可能耗时或产生接口费用，文档会明确指出首次发生的位置。
- 依赖示例采用具体版本，目的是便于教学复现，不宣称它们是最新版本。资料核对与代码验证日期：2026-09-16～17。

## 五、完整文件流向

Python 的永久文件在 step01～08 给全：`config` → `models` → `loader` → `splitter` → `embedding` → `vector_store` → `ingest` → `retriever` → `cli` → `schemas` → `main`。

C# 在 step09～11 给全：`RuntimeSettings`、`ChatUI` → `RagModels`、`HttpJson`、`RagClient` → `ChatModels`、`ChatClient`、`PromptBuilder`、`ChatController`。

`UiPreview` 和 `RetrievalPreview` 是阶段性演示组件，教程会明确让你从场景移除。测试文件是实际可运行的代码，不是仅用于展示的伪代码。

## 六、验证范围

教程发布时的实际检查记录见 [step12 的交付验证记录](step12.md#交付验证记录)。请区分：

- Python 语法检查与离线单元测试；
- 使用模拟对象的业务验证；
- 真实 BGE-M3、Qdrant、Unity 场景与 DeepSeek 的端到端测试。

前两项通过不代表最后一项已经实测。按照每篇运行命令，你会在自己的环境补齐最后一项。

下一步：[step01：环境与配置](step01.md)。
