# Step 12：检查质量，学会更新、重启和打包

[上一篇](step11.md) · [教程目录](README.md)

## 本篇目标与前置条件

第 11 篇已经能完成单轮问答。本篇回答三个实际问题：

1. 怎么知道检索和回答有没有进步？
2. 资料变化后，应该运行哪一步？
3. 下次开机、打包后，怎样再次使用？

先使用第 2 篇的合成校园资料，不混入自己的真实资料。本篇先运行固定题集，再人工核对生成回答，最后整理日常操作。没有参数扫描、自动调阈值或大型评测框架。

## 1. 将“找对资料”和“答对问题”分开

假设问题是“补校园卡需要什么证件”：

- 检索成功：返回了包含身份证、学生证的补卡记录。
- 回答成功：生成的回答正确列出证件，没有增加原文未提供的要求。
- 引用正确：回答所标的编号确实对应支持这些事实的原文。

找到了资料，后两项仍可能出错。只运行 Python 检索脚本，不能声称测出了整套问答准确率。

本篇使用一个容易理解的检索指标 **Hit@5**：对资料中有答案的问题，最终返回的至多 5 个片段中，是否包含至少一条预期来源。

```text
Hit@5 = 至少命中一条预期来源的有答案问题数 / 有答案问题总数
```

这里检查的是第 7 篇经过阈值和字符预算筛选后的结果。涉及两个来源的问题，即使只找到一个也会算“命中”；因此它不衡量资料覆盖是否完整，完整性需要下面的逐题核对。

## 2. 准备固定的 24 道题

新建 `data/evaluations` 目录，新建 `data/evaluations/questions.jsonl`：

<!-- file: data/evaluations/questions.jsonl -->
```jsonl
{"id":"c01","query":"图书馆几点关门？","expected":["campus/library.md#md:0:开放时间"],"facts":["22:00"]}
{"id":"c02","query":"图书馆星期天可以进去吗？","expected":["campus/library.md#md:0:开放时间"],"facts":["周日开放","8:00—22:00"]}
{"id":"c03","query":"图书馆在什么地方？","expected":["campus/library.md#md:1:所在位置"],"facts":["明德楼东侧"]}
{"id":"c04","query":"第一食堂早饭几点开始？","expected":["campus/canteen.txt#text"],"facts":["7:00"]}
{"id":"c05","query":"第一食堂有素食吗？","expected":["campus/canteen.txt#text"],"facts":["二楼素食窗口"]}
{"id":"c06","query":"校园卡补办需要带什么？","expected":["campus/services.json#card"],"facts":["身份证","学生证"]}
{"id":"c07","query":"补办校园卡要多少钱？","expected":["campus/services.json#card"],"facts":["20元"]}
{"id":"c08","query":"校园网络报修在哪里？","expected":["campus/services.json#network"],"facts":["信息楼203室"]}
{"id":"c09","query":"图书馆什么时候开门，在哪里？","expected":["campus/library.md#md:0:开放时间","campus/library.md#md:1:所在位置"],"facts":["8:00","明德楼东侧"]}
{"id":"c10","query":"明天会下雨吗？","expected":[],"facts":["资料不足"]}
{"id":"c11","query":"本学期校长叫什么名字？","expected":[],"facts":["资料不足"]}
{"id":"c12","query":"校园游泳馆门票多少钱？","expected":[],"facts":["资料不足"]}
{"id":"t01","query":"晚上九点还能去图书馆吗？","expected":["campus/library.md#md:0:开放时间"],"facts":["可以","22:00关门"]}
{"id":"t02","query":"周六图书馆开到几点？","expected":["campus/library.md#md:0:开放时间"],"facts":["22:00"]}
{"id":"t03","query":"我想自习，图书馆哪一层有自习区？","expected":["campus/library.md#md:1:所在位置"],"facts":["二楼"]}
{"id":"t04","query":"第一食堂中午十二点供应午餐吗？","expected":["campus/canteen.txt#text"],"facts":["供应","11:00—13:00"]}
{"id":"t05","query":"第一食堂在生活区哪边？","expected":["campus/canteen.txt#text"],"facts":["南侧"]}
{"id":"t06","query":"补校园卡去服务楼几楼？","expected":["campus/services.json#card"],"facts":["一楼"]}
{"id":"t07","query":"校园卡补办下午几点结束？","expected":["campus/services.json#card"],"facts":["工作日17:00"]}
{"id":"t08","query":"校园网络报修要提供哪些信息？","expected":["campus/services.json#network"],"facts":["学号","故障描述"]}
{"id":"t09","query":"补卡和网络报修分别去哪里？","expected":["campus/services.json#card","campus/services.json#network"],"facts":["服务楼一楼","信息楼203室"]}
{"id":"t10","query":"第一食堂晚餐几点开始？","expected":[],"facts":["资料没有晚餐时间"]}
{"id":"t11","query":"图书馆联系电话是多少？","expected":[],"facts":["资料没有电话"]}
{"id":"t12","query":"校园卡补办可以微信付款吗？","expected":[],"facts":["资料没有付款方式"]}
```

JSONL 是“一行一个 JSON 对象”，最外层不要再加数组方括号，也不要在行尾添加逗号。

这套题沿用原教程的 24 道校园问题，但先作为一份固定练习集使用，不再要求学习分组、阈值扫描和命令行参数。c/t 只是保留的题号前缀，本篇不按前缀分组。

| 字段 | 用途 |
| --- | --- |
| id | 方便定位是哪道题 |
| query | 输入问题 |
| expected | 预期来源，格式为“相对路径#record_key” |
| facts | 人工检查最终回答时应看到的事实 |

`expected=[]` 表示当前资料里没有足够答案，不表示数据库一定返回空列表。检索仍可能找到相似但无用的内容。

使用来源和 record_key，不使用 chunk_id，因为重新切块会改变片段标识。Markdown 的 record_key 含章节顺序；改变章节顺序或改名后，应同步核对题集中的 expected。

## 3. 编写简单评估脚本

新建 `backend/scripts/evaluate_retrieval.py`：

<!-- file: backend/scripts/evaluate_retrieval.py -->
```python
import json

from backend.app.config import ROOT, load_settings


def summarize(rows):
    answerable = 0
    hits = 0
    for row in rows:
        expected = set(row['expected'])
        if not expected:
            continue
        found = {
            hit['source'] + '#' + hit['record_key']
            for hit in row['results']
        }
        answerable += 1
        hits += bool(expected & found)
    return {
        'answerable_count': answerable,
        'hit_count': hits,
        'hit_at_5': hits / answerable if answerable else None,
    }


def main():
    from backend.app.embedding import EmbeddingService
    from backend.app.retriever import Retriever
    from backend.app.vector_store import VectorStore

    source = ROOT / 'data/evaluations/questions.jsonl'
    examples = [
        json.loads(line)
        for line in source.read_text(encoding='utf-8-sig').splitlines()
        if line.strip()
    ]
    settings = load_settings()
    model = EmbeddingService(settings)
    store = VectorStore(settings)
    rows = []
    try:
        retriever = Retriever(settings, model, store)
        for example in examples:
            response = retriever.retrieve(example['query'], top_k=5)
            row = {**example, 'results': response['results']}
            rows.append(row)
            found = [
                hit['source'] + '#' + hit['record_key']
                for hit in row['results']
            ]
            print(example['id'], found, flush=True)
    finally:
        store.close()

    report = {
        'top_k': 5,
        'max_context_chars': settings.max_context_chars,
        'min_score': settings.min_score,
        'summary': summarize(rows),
        'rows': rows,
    }
    output = ROOT / 'data/evaluations/report.json'
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    print('逐题结果：', output)


if __name__ == '__main__':
    main()
```

按顺序看代码：

1. 读取每一行题目。
2. 加载一次模型，复用同一个数据库连接。
3. 每题调用现成 Retriever，不再实现一套检索。
4. 保留完整原文与来源，计算命中比例。
5. 输出一份可阅读的 JSON 报告。

`{**example, ...}` 表示保留题目原字段，再添加结果；集合交集 `expected & found` 用来判断有没有共同来源。`None` 表示题集里没有有答案问题，比例无从计算，不能把这种情况写成 0% 或 100%。

只有基本报告字段，没有执行编号、耗时统计、索引指纹或历史报告管理。报告会覆盖上一次结果；需要对比时，自己将上一份报告另存为另一个名字即可。

## 4. 运行并读懂结果

先退出 Unity Play，停止 FastAPI 和 CLI，让模型只在本次评估进程加载一次。Qdrant 保持运行。确认：

- 使用第 2 篇的三份资料，并已按第 6 篇导入。
- `MAX_CONTEXT_CHARS=4000`。
- `MIN_SCORE` 未设置。
- `CHUNK_SIZE=600`、`CHUNK_OVERLAP=100`；若改变过切分参数，需要重新导入。

执行：

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.evaluate_retrieval
```

逐题输出的来源顺序就是返回顺序。结束后打开 `data/evaluations/report.json`，查看 summary 和 rows。CPU 较慢时等待当前题完成，不要连续启动多个评估进程。

固定题集有 18 道有答案问题，6 道资料不足问题。因此 `answerable_count` 应为 18；具体命中数要以你实际运行结果为准。

**本例只有约 5 个片段，top_k=5 很容易把整个小知识库全部取回。即使命中率达到 100%，也主要证明流程接通了，不能证明排序准确。** 读报告时继续检查：

- c01 的开放时间资料排在哪里？
- c09 是否同时找到开放时间和位置？
- t09 是否同时找到补卡和报修地点？
- t10～t12 返回的相似原文，真的包含所问事实吗？

想观察排序，可以暂时在 CLI 或 Unity 使用 top_k=1；这只用于手动对比，不改本篇固定 Hit@5 的计算口径。正式比较检索方案前，应补充更多互不相同的文档，让候选片段明显多于 5，并增加相应问题。

用同一批题反复调整参数后得到的分数，只能说明这批练习题的表现。需要判断对新问题是否有效时，另外保留未参与调参的问题；这可以在熟悉本篇后再做。

## 5. 补三项小测试，验证关键逻辑

这些测试验证少量容易出错的规则，不运行真实向量模型和聊天调用。之前第 2、3、6、8 篇的小测试继续保留。

### 5.1 指标不要算错分母

新建 `backend/tests/test_evaluation.py`：

<!-- file: backend/tests/test_evaluation.py -->
```python
import unittest
from backend.scripts.evaluate_retrieval import summarize


class EvaluationTests(unittest.TestCase):
    def test_hit_rate_uses_only_answerable_questions(self):
        rows = [
            {'expected': ['a#1', 'b#2'],
             'results': [{'source': 'a', 'record_key': '1'}]},
            {'expected': ['c#3'], 'results': []},
            {'expected': [],
             'results': [{'source': 'a', 'record_key': '1'}]},
        ]
        result = summarize(rows)
        self.assertEqual(result['answerable_count'], 2)
        self.assertEqual(result['hit_count'], 1)
        self.assertEqual(result['hit_at_5'], 0.5)


if __name__ == '__main__':
    unittest.main()
```

两道有答案问题中命中一道，所以是 0.5。第三道无答案题不参与这个比例；第一道需要两个来源但只命中一个，也能通过 Hit@5，这正好展示指标的局限。

### 5.2 预算不要把原文截断后误当完整资料

新建 `backend/tests/test_retriever.py`：

<!-- file: backend/tests/test_retriever.py -->
```python
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.app.retriever import Retriever


class RetrieverTests(unittest.TestCase):
    def test_budget_keeps_complete_text_and_source(self):
        settings = SimpleNamespace(
            top_k=5, min_score=None, max_context_chars=3)
        model = Mock()
        model.encode_query.return_value = [1.0, 0.0]
        rows = [
            SimpleNamespace(
                id='first', score=0.9,
                payload=dict(source='a.txt', title='甲', section='',
                             record_key='text', text='甲乙丙')),
            SimpleNamespace(
                id='second', score=0.8,
                payload=dict(source='b.txt', title='乙', section='',
                             record_key='text', text='丁戊己')),
        ]
        store = Mock()
        store.query.return_value = rows
        result = Retriever(settings, model, store).retrieve(' 问题 ')
        model.encode_query.assert_called_once_with('问题')
        store.query.assert_called_once_with([1.0, 0.0], 5)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['text'], '甲乙丙')
        self.assertEqual(result['results'][0]['source'], 'a.txt')


if __name__ == '__main__':
    unittest.main()
```

这里的 Mock 只是测试替身，返回预先准备的数据。预算只够第一块时，第二块整块跳过；留下的正文和来源必须对应。它不模拟复杂故障，也不能验证语义相似度。

### 5.3 验证向量和正文配对，以及重建效果

新建 `backend/tests/test_vector_store.py`：

<!-- file: backend/tests/test_vector_store.py -->
```python
import unittest
from types import SimpleNamespace
from qdrant_client import QdrantClient

from backend.app.loader import make_document
from backend.app.splitter import split_document
from backend.app.vector_store import VectorStore


class VectorStoreTests(unittest.TestCase):
    def test_write_query_and_rebuild(self):
        settings = SimpleNamespace(qdrant_collection='test_index')
        store = VectorStore(settings, client=QdrantClient(':memory:'))
        first = split_document(
            make_document('a.txt', '甲', '', 'text', '甲资料'))[0]
        second = split_document(
            make_document('b.txt', '乙', '', 'text', '乙资料'))[0]
        try:
            store.rebuild(3)
            store.upsert(
                [first, second],
                [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
            self.assertEqual(store.count(), 2)
            hit = store.query([1.0, 0.0, 0.0], 1)[0]
            self.assertEqual(hit.payload['source'], 'a.txt')
            self.assertEqual(hit.payload['text'], '甲资料')
            store.rebuild(3)
            store.upsert([second], [[0.0, 1.0, 0.0]])
            self.assertEqual(store.count(), 1)
            self.assertEqual(
                store.query([0.0, 1.0, 0.0], 1)[0].payload['source'],
                'b.txt')
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
```

`QdrantClient(':memory:')` 使用客户端本地内存模式。它能检查本教程的存取调用和 payload 配对，但不能代替第 5 篇的 Docker 连接与持久化验收。

一次运行这些教程测试：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_loader backend.tests.test_splitter backend.tests.test_token_budget backend.tests.test_api backend.tests.test_retriever backend.tests.test_vector_store backend.tests.test_evaluation -v
```

逐项通过后，不需要为了“更完整”不停加入框架。测试只能证明各自检查的规则；真实中文检索仍看题集，最终答案仍需人工核对。

## 6. 人工检查完整问答

重新启动 FastAPI，在 Unity 进入 Play。先抽查 c01、c06、c09、t09、t10、t11，时间允许再完成 24 题。

在自己的笔记里用以下表格记录即可，不必开发新的评测界面：

| 题号 | 关键原文是否找到 | 回答事实是否正确 | 引用是否支持事实 | 是否添加无依据内容 |
| --- | --- | --- | --- | --- |
| c01 | 自己填写 | 自己填写 | 自己填写 | 自己填写 |
| c09 | 两个来源都检查 | 时间和地点都检查 | 各自对应来源 | 自己填写 |
| t10 | 可能找到食堂介绍 | 应说明没有晚餐时间 | 不能拿早餐时间当晚餐依据 | 不得编造晚餐时间 |

facts 是核对提示，不做字符串逐字匹配。例如“晚上十点”与“22:00”含义相同；一句回答同时包含正确词和错误结论，简单匹配仍会误判。

发现问题时按最短路径修正：

| 发现的问题 | 优先回看 |
| --- | --- |
| 数据库没有这份资料 | 第 2、6 篇：读取和导入 |
| 有资料但排得很后 | 第 3、4、7 篇：分段、编码与检索 |
| 两个事实只找到一个 | 原文是否被切散，top_k 与预算是否足够 |
| 正文有答案但模型答错 | 第 11 篇：消息和回答规则 |
| 编造资料没有的内容 | 资料不足规则和逐条事实核对 |
| 请求失败却当作无答案 | 第 8、10、11 篇：错误提示 |

每次只改一个主要因素，再用相同题目比较。否则很难知道是哪项修改起了作用。

## 7. 修改资料后怎么更新

本教程采用全量重建，顺序固定：

1. 退出 Unity Play。
2. 在 FastAPI 终端 Ctrl+C 停止服务；CLI 和评估进程也退出。
3. 修改 `data/documents` 下的资料。
4. 重新运行导入。
5. 等导入成功，再启动 API 和 Unity。

第四步的命令：

```powershell
.\.venv\Scripts\python.exe -m backend.ingest
```

全量重建会替换 `rag_tutorial` 中的索引；原始文件仍保留。向量准备好之后才开始重建集合，但重建到写入之间仍可能失败。本教程不提供旧版本自动回滚，修正问题后重新导入即可。更新期间不要查询。

做两次实际观察：

- 把图书馆关门时间临时改为 21:00，重新导入再提问，确认新答案来自新原文；实验后恢复 22:00 并再次导入，保持题集正确。
- 在资料目录添加一份独立的临时 TXT，导入后确认可检索；删除这份临时文件，再导入，确认该来源不再出现在结果中。

只删除文件而不重新导入，数据库不会自动知道变化。这正是需要第二次导入的原因。

| 修改项 | 需要重新导入吗 |
| --- | --- |
| 原文、文档路径、标题或章节 | 需要 |
| CHUNK_SIZE、CHUNK_OVERLAP、向量输入拼接方式 | 需要 |
| 向量模型或模型版本、MAX_TOKENS | 需要，查询侧也要使用匹配设置 |
| TOP_K、MIN_SCORE、MAX_CONTEXT_CHARS | 不需要，只重启读取配置的 Python 进程 |
| Unity 的 topK、聊天模型、key 或超时 | 不需要，退出并重新进入 Play |
| 提示词或 UI 排版 | 不需要重新建索引 |

## 8. 下次开机的最短启动顺序

资料和索引没变时，不必重新安装依赖或重新导入：

1. 启动 Docker Desktop，等待引擎就绪。
2. 从项目根目录启动 Qdrant。
3. 启动 FastAPI，等待模型加载完成。
4. 打开 Unity 项目，运行场景。

第二步：

```powershell
docker compose -f infra/compose.yaml up -d
Invoke-RestMethod 'http://127.0.0.1:6333/collections'
```

第三步，在一个保持打开的终端运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

另一个终端可检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/health'
```

health 只能说明进程能响应。最后再问一次已知问题，确认依赖和资料仍正常。

结束学习时先退出 Play，再 Ctrl+C 停止 API；暂时不使用数据库时：

```powershell
docker compose -f infra/compose.yaml stop
```

保留命名卷，下次仍用原来的数据。不需要在日常停止时删除容器数据卷。

## 9. 可选：打包为本机 Windows 程序

这是把已完成界面交付成可执行程序的练习，不是把 Python、模型和数据库自动打包进去。

在 Unity 2022.3 中：

1. 保存当前场景。
2. 打开 File → Build Settings → Add Open Scenes。
3. 选择 PC, Mac & Linux Standalone，Target Platform 为 Windows，Architecture 为 x86_64。
4. Player Settings → Other Settings：本教程先使用 Mono、.NET Standard 2.1；不在这一篇增加 IL2CPP 配置。
5. 核对 Company Name 与 Product Name，它们会影响本机持久化目录。
6. 点击 Build，将输出放到 `unity-client/Builds/Windows`。
7. 先启动 Qdrant 和 API，再运行生成的程序。

不要只拷贝一个 exe，保留同目录的全部构建输出。程序仍访问 `127.0.0.1:8000`，所以这一版要求后端也运行在同一台电脑上。

打包后的配置仍由 RuntimeSettings 放在 `Application.persistentDataPath`。若找不到，查看 Unity 的 Player.log 中“本机配置文件”那一行；Windows 通常位于 `%USERPROFILE%\AppData\LocalLow\<CompanyName>\<ProductName>\Player.log`。以实际日志打印为准，不假定编辑器与打包目录必然相同。

首次运行创建配置后，退出程序，填写该目录中的 key，再启动。不要把个人密钥作为打包资源交给别人。若以后要给其他用户发布，再单独学习由服务端保存密钥的方案。

## 10. 你现在可以解释整个系统了

试着不看代码回答：

1. 为什么一个文件可以产生多个 Document？
2. 为什么 Chunk 里既有正文又有来源？
3. 为什么查询与文档必须使用同一个向量模型？
4. Qdrant 返回原文后，聊天模型还需要做什么？
5. 为什么“返回了五条”不等于“找到了五条正确答案”？
6. 为什么更新资料后需要重建索引？
7. 生成回答有引用编号，为什么仍需核对？

能够回答这些问题，再尝试一项扩展就好，例如导入自己的小批资料、改善分段、加入重排序或学习多轮问答。不要一次把所有扩展搬进当前代码。

## 为什么这么设计

评估放在完整问答之后，是为了让你已经亲眼见过“检索错”和“回答错”，再理解指标到底测什么。固定题集、一个简单比例和人工核对，足以形成“发现问题—修改—再检查”的学习过程。

日常更新采用停止查询后重建，界面采用单轮问答。这些明确的使用约定减少了发布版本、并发更新、回滚和历史管理代码，让你先掌握 RAG 的主要数据流。

## 最终验收与本次文档的验证范围

- [ ] 阅读后能按章节自己创建源码并运行。
- [ ] 轻量测试通过，已知问题能检索到正确原文。
- [ ] 已人工核对有答案、跨来源和资料不足三类问题。
- [ ] 能独立停止、更新索引、重启程序。
- [ ] 知道 Hit@5 不等于答案准确率，小资料库的高分也不说明泛化能力。
- [ ] 知道示例只适用于当前单人、本机、小资料学习范围。

这次交付只编写 Markdown；上面的代码、命令和预期结果是供你实际操作的教程，不是已经在本次交付中运行通过的记录。未实际调用聊天服务、启动模型、构建 Unity 或创建这些代码块所描述的文件。

完成本篇后，回到 [教程目录](README.md)，用自己的话沿着数据流复述一次整个项目。
