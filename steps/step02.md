# Step 02：读取 TXT、Markdown 和 JSON

## 目标与前置条件

已完成 [step01](step01.md)。本篇不需要 Docker 或模型。我们先把文件转换成统一的 `Document`，再处理“语义检索”。

每个数据单元必须知道自己来自哪个文件。两个目录中即使都有 `notice.txt`，也不能混为同一来源。

## 1. 创建合成知识资料

所有内容均为虚构教学资料，不代表任何学校的实际规定。在 PyCharm 按下列路径新建文件。

### 新建 `data/documents/campus/library.md`

<!-- file: data/documents/campus/library.md -->
```markdown
# 星河校园图书馆（合成测试资料）

## 开放时间
星河校园图书馆周一至周日每天 8:00 开门，22:00 关门。周末开放时间相同。

## 所在位置
星河校园图书馆位于明德楼东侧。自习区在图书馆二楼。
```

### 新建 `data/documents/campus/canteen.txt`

<!-- file: data/documents/campus/canteen.txt -->
```text
星河校园食堂（合成测试资料）

第一食堂位于生活区南侧。早餐供应时间为 7:00 至 9:00，午餐为 11:00 至 13:00。
第一食堂二楼设有素食窗口。该文档没有提供晚餐时间。
```

### 新建 `data/documents/campus/services.json`

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

JSON 必须使用英文双引号，不允许末尾多余逗号。本教程支持对象或对象数组；正文采用 `content`，其他业务字段转换成“字段名：值”。不支持任意嵌套对象，遇到它会报错，便于你发现格式问题。

## 2. 新建统一数据结构

文件：`backend/app/models.py`。把后续会用到的片段与检索结构一起定义，但本篇只使用 Document。

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


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    source: str
    title: str
    section: str
    record_key: str
    chunk_index: int
    text: str
    content_hash: str

    def payload(self):
        return asdict(self)


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    source: str
    title: str
    section: str
    record_key: str
    text: str
    score: float


class ServiceUnavailable(RuntimeError):
    pass
```

`dataclass` 自动生成初始化方法。`frozen=True` 防止代码意外修改来源信息。SHA256 用于比较内容是否变化；UUID 用作稳定标识，不是随机每运行一次就换一个。

## 3. 新建加载器

文件：`backend/app/loader.py`。

<!-- file: backend/app/loader.py -->
```python
from dataclasses import dataclass, field
from pathlib import Path
import json
import re

from backend.app.models import Document, digest, stable_id


@dataclass
class LoadReport:
    documents: list[Document] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)


def make_document(source, title, section, key, text):
    text = text.strip()
    return Document(
        document_id=stable_id(source + '#' + key),
        source=source, title=title, section=section,
        record_key=key, text=text, content_hash=digest(text),
    )


def markdown_documents(source, raw):
    title = Path(source).stem
    headings = []
    section = ''
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
    documents = []
    keys = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f'条目 {index} 必须是对象')
        title = row.get('title', Path(source).stem)
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f'条目 {index} 的 title 必须是非空文字')
        key = str(row.get('id', f'row:{index}'))
        if key in keys:
            raise ValueError(f'重复 id：{key}')
        keys.add(key)
        parts = []
        for name, value in row.items():
            if name in ('id', 'title') or value is None:
                continue
            if isinstance(value, (dict, list)):
                raise ValueError(f'条目 {index} 的 {name} 不支持嵌套对象或数组')
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(f'无法处理字段 {name}')
            text = str(value).strip()
            if text:
                parts.append(f'{name}：{text}')
        if not parts:
            raise ValueError(f'条目 {index} 没有正文')
        documents.append(make_document(source, title.strip(), '', key, '\n'.join(parts)))
    return documents


def load_documents(root: Path) -> LoadReport:
    root = root.resolve()
    report = LoadReport()
    if not root.is_dir():
        report.errors.append(f'资料目录不存在：{root}')
        return report
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        source = path.relative_to(root).as_posix()
        if path.suffix.lower() not in ('.txt', '.md', '.json'):
            report.skipped.append(source + '：格式不支持')
            continue
        try:
            raw = path.read_text(encoding='utf-8-sig')
            if not raw.strip():
                raise ValueError('文件为空')
            if path.suffix.lower() == '.md':
                docs = markdown_documents(source, raw)
            elif path.suffix.lower() == '.json':
                docs = json_documents(source, raw)
            else:
                docs = [make_document(source, path.stem, '', 'text', raw)]
            if not docs:
                raise ValueError('没有可导入的正文')
            report.documents.extend(docs)
            report.files[source] = digest(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            report.errors.append(f'{source}：{exc}')
    return report


if __name__ == '__main__':
    from backend.app.config import load_settings
    report = load_documents(load_settings().documents_dir)
    for doc in report.documents:
        print(f'{doc.source} | {doc.record_key} | {len(doc.text)} 字')
        print(doc.text)
    print('文件数：', len(report.files), '文档单元：', len(report.documents))
    print('跳过：', report.skipped, '错误：', report.errors)
    raise SystemExit(1 if report.errors else 0)
```

### 为什么一个文件会变成多个 Document

Markdown 的章节、JSON 的业务条目有各自完整语义，所以先按它们拆成文档单元。下一篇再按长度切成 Chunk。`LoadReport.errors` 集中收集错误，避免第一个坏文件让其余文件连检查机会都没有；正式导入时，任何解析错误会阻止发布新索引。

这里是教学用的轻量 Markdown 标题解析，不是完整 Markdown 渲染器。代码块中的 `#` 不应被当成标题，因此单独跟踪围栏状态。

## 4. 新建加载器测试

文件：`backend/tests/test_loader.py`。测试只使用系统临时目录，不改你的资料。

<!-- file: backend/tests/test_loader.py -->
```python
import tempfile
import unittest
from pathlib import Path

from backend.app.loader import load_documents


class LoaderTests(unittest.TestCase):
    def test_sources_bom_and_json_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'a').mkdir()
            (root / 'b').mkdir()
            (root / 'a/x.txt').write_text('中文资料', encoding='utf-8-sig')
            (root / 'b/x.txt').write_text('另一份', encoding='utf-8')
            (root / 'x.json').write_text(
                '[{"id":"a","content":"甲"},{"id":"b","content":"乙"}]',
                encoding='utf-8',
            )
            report = load_documents(root)
            self.assertFalse(report.errors)
            self.assertEqual(len(report.documents), 4)
            self.assertEqual(len({d.document_id for d in report.documents}), 4)
            self.assertNotIn('\ufeff', report.documents[0].text)

    def test_bad_files_do_not_hide_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'bad.json').write_text('{', encoding='utf-8')
            (root / 'empty.txt').write_text('', encoding='utf-8')
            (root / 'skip.pdf').write_bytes(b'fake')
            report = load_documents(root)
            self.assertEqual(len(report.errors), 2)
            self.assertEqual(len(report.skipped), 1)

    def test_markdown_sections_and_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'x.md').write_text('# 总标题\n## 时间\n八点\n## 地点\n一楼', encoding='utf-8')
            first = load_documents(root).documents
            self.assertEqual([d.section for d in first], ['时间', '地点'])
            self.assertEqual(first, load_documents(root).documents)


if __name__ == '__main__':
    unittest.main()
```

## 5. 运行与预期结果

终端 A，项目根目录：

```powershell
.\.venv\Scripts\python.exe -m backend.app.loader
.\.venv\Scripts\python.exe -m unittest backend.tests.test_loader -v
```

使用这三份资料应得到 **3 个文件、5 个文档单元、0 个错误**，单元依次包含食堂、图书馆两个章节、两个服务条目。单元顺序按文件名排序，长度以实际运行结果为准。单元测试应全部通过。

| 现象 | 原因与处理 |
| --- | --- |
| JSON 报 Expecting property name | 检查中文引号、尾逗号；PyCharm 通常会标红 |
| 中文无法解码 | 将资料保存为 UTF-8，不能用 `errors=ignore` 静默丢字 |
| 文档数不是 5 | 检查是否加入其他文档、遗漏章节标题或 JSON 数组条目 |
| 只有标题的 Markdown 报错 | 没有正文是错误，补充真实内容 |

- [ ] 能打印原文，内容未丢失。
- [ ] Markdown 标题和 JSON 条目独立保留。
- [ ] 测试通过，错误不会被静默跳过。

下一篇：[step03：把文档切成片段](step03.md)。
