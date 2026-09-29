# Step 2：读取文档，让每段文字保留来源

[上一篇](step1.md) · [教程目录](README.md) · [下一篇](step3.md)

## 本篇目标与前置条件

完成第 1 篇。现在把磁盘里的文件变成 Python 可以处理的数据。先用一份 TXT 看结果，再添加 Markdown 和 JSON。全篇不需要模型、数据库或网络服务。

本篇流程：**文件 → 读取文字 → 按章节或条目整理 → Document 列表**。

## 1. 先准备一份 TXT

新建 `data/documents/campus/canteen.txt`。以下校园信息都是虚构的教学数据：

<!-- file: data/documents/campus/canteen.txt -->
```text
星河校园食堂（合成测试资料）

第一食堂位于生活区南侧。早餐供应时间为 7:00 至 9:00，午餐为 11:00 至 13:00。
第一食堂二楼设有素食窗口。该文档没有提供晚餐时间。
```

TXT 没有固定结构，初步把整个文件看作一个文档单元。下一篇再按长度切分。

## 2. 定义 Document

新建 `backend/app/models.py`：

<!-- file: backend/app/models.py -->
```python
from dataclasses import dataclass, asdict
from hashlib import sha256
from uuid import uuid5, NAMESPACE_URL


def digest(text: str) -> str:
    return sha256(text.encode('utf-8')).hexdigest()


def stable_id(text: str) -> str:
    return str(uuid5(NAMESPACE_URL, 'rag-tutorial/' + text))


@dataclass(frozen=True)
class Document:
    document_id: str
    source: str
    title: str
    section: str
    record_key: str
    text: str
    content_hash: str
```

`@dataclass` 会帮你生成构造函数。把 `Document` 想成一张记录卡：

| 字段 | 为什么需要 |
| --- | --- |
| `text` | 真正被检索的原文 |
| `source` | 原文来自哪个相对路径，如 `campus/canteen.txt` |
| `title`、`section` | 文件主题与章节，帮助理解片段 |
| `record_key` | 同一文件里是哪条业务记录 |
| `document_id` | 文档单元的稳定标识 |
| `content_hash` | 当前正文的摘要，后面用于构造片段标识 |

`digest()` 把内容变成摘要；`stable_id()` 将来源信息变成 UUID。它们负责标识数据，不负责计算语义相似度。相似度要到第 4 篇才出现。

`frozen=True` 表示建立记录后不原地改字段。后面切分时会新建片段，不把原文改掉。

## 3. 写加载器，先运行 TXT

新建 `backend/app/loader.py`：

<!-- file: backend/app/loader.py -->
```python
from pathlib import Path
import json
import re

from backend.app.models import Document, digest, stable_id


def make_document(source, title, section, key, text):
    text = text.strip()
    return Document(
        document_id=stable_id(source + '#' + key),
        source=source, title=title, section=section,
        record_key=key, text=text, content_hash=digest(text),
    )


def markdown_documents(source, raw):
    title = Path(source).stem
    section = ''
    headings = []
    buffer = []
    result = []
    in_fence = False

    def flush():
        text = '\n'.join(buffer).strip()
        if text:
            key = f'md:{len(result)}:{section}'
            result.append(make_document(source, title, section, key, text))
        buffer.clear()

    for line in raw.splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            in_fence = not in_fence
        match = None if in_fence else re.match(r'^(#{1,6})\s+(.+?)\s*$', line)
        if not match:
            buffer.append(line)
            continue
        flush()
        level, label = len(match[1]), match[2]
        if level == 1:
            title = label
        headings = [(n, h) for n, h in headings if n < level]
        headings.append((level, label))
        section = ' / '.join(h for n, h in headings if n > 1)
    flush()
    return result


def json_documents(source, raw):
    data = json.loads(raw)
    rows = data if isinstance(data, list) else [data]
    result = []
    keys = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f'第 {index + 1} 条必须是 JSON 对象')
        title = row.get('title', Path(source).stem)
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f'第 {index + 1} 条的 title 必须是非空字符串')
        key = str(row.get('id', f'row:{index}'))
        if key in keys:
            raise ValueError(f'重复 id：{key}')
        keys.add(key)
        parts = []
        for name, value in row.items():
            if name in ('id', 'title') or value is None:
                continue
            if isinstance(value, (dict, list)):
                raise ValueError(f'字段 {name} 暂不支持嵌套对象或数组')
            text = str(value).strip()
            if text:
                parts.append(f'{name}：{text}')
        if not parts:
            raise ValueError(f'第 {index + 1} 条没有正文')
        result.append(make_document(source, title.strip(), '', key, '\n'.join(parts)))
    return result


def load_documents(root: Path) -> list[Document]:
    if not root.is_dir():
        raise FileNotFoundError(f'资料目录不存在：{root}')
    documents = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.txt', '.md', '.json'):
            continue
        source = path.relative_to(root).as_posix()
        try:
            raw = path.read_text(encoding='utf-8-sig')
            if not raw.strip():
                raise ValueError('文件没有正文')
            if path.suffix.lower() == '.md':
                items = markdown_documents(source, raw)
            elif path.suffix.lower() == '.json':
                items = json_documents(source, raw)
            else:
                items = [make_document(source, path.stem, '', 'text', raw)]
            if not items:
                raise ValueError('没有可用的文档单元')
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f'{source}：{exc}') from exc
        documents.extend(items)
    return documents


if __name__ == '__main__':
    from backend.app.config import load_settings
    docs = load_documents(load_settings().documents_dir)
    for doc in docs:
        print(f'{doc.source} | {doc.record_key}')
        print(doc.text)
        print()
    print('文档单元数：', len(docs))
```

第一遍先顺着 `load_documents()` 看 TXT 分支：找到文件 → 读取 UTF-8 → `make_document()` → 放入列表。Markdown、JSON 两个函数在下一小节结合样例再看。

遇到坏文件会停止并说明来源，不建立错误报告、重试或自动修复系统。`utf-8-sig` 同时兼容普通 UTF-8 与带 BOM 的 UTF-8。

先运行：

```powershell
.\.venv\Scripts\python.exe -m backend.app.loader
```

如果资料目录只有这一份 TXT，应显示食堂原文和 `文档单元数：1`。先确认能看到自己刚写的文字，再继续添加格式。

## 4. 再加入 Markdown 和 JSON

新建 `data/documents/campus/library.md`：

<!-- file: data/documents/campus/library.md -->
```markdown
# 星河校园图书馆（合成测试资料）

## 开放时间
星河校园图书馆周一至周日每天 8:00 开门，22:00 关门。周末开放时间相同。

## 所在位置
星河校园图书馆位于明德楼东侧。自习区在图书馆二楼。
```

新建 `data/documents/campus/services.json`：

<!-- file: data/documents/campus/services.json -->
```json
[
  {
    "id": "card",
    "title": "校园卡补办（合成测试资料）",
    "content": "校园卡补办在服务楼一楼办理，需携带身份证和学生证。",
    "办理时间": "工作日 9:00 至 17:00",
    "费用": "20 元"
  },
  {
    "id": "network",
    "title": "校园网络报修（合成测试资料）",
    "content": "校园网络报修地点是信息楼 203 室。",
    "服务时间": "工作日 9:00 至 18:00",
    "说明": "报修时提供学号和故障描述。"
  }
]
```

再次运行加载器，只有这三份资料时应得到 **5 个 Document**：

| 文件 | 文档单元数 |
| --- | ---: |
| 食堂 TXT | 1 |
| 图书馆 Markdown | 2：开放时间、所在位置 |
| 服务 JSON | 2：补卡、网络报修 |

现在回看两个解析函数：

- Markdown 遇到标题，就把前面的正文收集为一个单元；`flush()` 是“把缓冲文字存下来”。标题列表保存章节层级，代码围栏内的 `#` 按普通文字处理。这是轻量标题解析，不是完整 Markdown 解析器。
- JSON 数组中的每个对象是一条业务记录。`id` 定位记录，`title` 表达主题，其余普通字段转换为“字段名：值”，因此“费用：20 元”不会失去“费用”的含义。
- 这一版 JSON 只支持单个对象或对象数组，业务字段为文字、数字或布尔值；嵌套结构先在资料中整理成平铺字段。

没有 `id` 时使用行号，没有标题时使用文件名；自己维护资料时建议明确填写 `id` 和 `title`。

## 5. 运行成功后再补两项小测试

新建 `backend/tests/test_loader.py`：

<!-- file: backend/tests/test_loader.py -->
```python
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.app.loader import load_documents


class LoaderTests(unittest.TestCase):
    def test_txt_sources_and_bom(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'a').mkdir()
            (root / 'b').mkdir()
            (root / 'a/x.txt').write_text('中文资料', encoding='utf-8-sig')
            (root / 'b/x.txt').write_text('另一份资料', encoding='utf-8')
            docs = load_documents(root)
            self.assertEqual([d.source for d in docs], ['a/x.txt', 'b/x.txt'])
            self.assertEqual(docs[0].text, '中文资料')

    def test_markdown_and_json_units(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'x.md').write_text(
                '# 图书馆\n## 时间\n八点开门\n## 地点\n一楼', encoding='utf-8')
            (root / 'x.json').write_text(
                '[{"id":"a","content":"补卡"},{"id":"b","content":"报修"}]',
                encoding='utf-8')
            docs = load_documents(root)
            self.assertEqual(len(docs), 4)
            self.assertEqual(len({d.document_id for d in docs}), 4)
            self.assertEqual([d.section for d in docs if d.source == 'x.md'],
                             ['时间', '地点'])


if __name__ == '__main__':
    unittest.main()
```

运行：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_loader -v
```

预期 2 项通过。`TemporaryDirectory` 只创建临时测试资料，不会改动校园文件。第一项检查同名文件来源与 BOM；第二项检查章节和条目独立保留。

## 为什么这么设计

RAG 最后需要告诉你“回答依据哪份资料”，所以来源必须从读取文件开始保留。先按章节和业务条目分开，再按长度切块，可以减少“补卡费用”和“网络报修地点”被混成一段的情况。

TXT 先跑通、再看另外两种格式，可以让你先理解读取主线，再理解格式差异。格式检查针对外部文件，能直接指出手敲 JSON 的常见错误，保留下来比让错误传到模型里更容易排查。

## 排查与验收

| 现象 | 处理 |
| --- | --- |
| JSON 解析失败 | 检查英文双引号、末尾逗号和方括号 |
| 单元数不是 5 | 检查资料目录是否混入其他文件，或漏了 `##` 标题 |
| 中文解码错误 | 将文件保存为 UTF-8，不要忽略解码错误 |
| PDF 没有导入 | 当前只读取 TXT、MD、JSON；PDF 支持属于后续扩展 |

- [ ] 能说明一个 Markdown 文件为什么可能对应多个 Document。
- [ ] 能从输出找到原文和相对来源。
- [ ] 两项测试通过。
- [ ] 知道此时只是整理文字，还没有语义检索。

---

完成验收后进入 [step3](step3.md)。
