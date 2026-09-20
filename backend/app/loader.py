import json
from dataclasses import dataclass, field
from pathlib import Path
import re

from backend.app.models import Document, stable_id, digest


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
        record_key=key, text=text, content_hash=digest(text)
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
        if line.lstrip().startswitch(('```', '~~~')):
            in_fence = not in_fence
        match = None if in_fence else re.match(r'^(#{1,6})\s+(.+?)\s*$', line)
        if not match:
            buffer.append(line)
            continue
        flush()
        level, label = len(match[1]), match[2]
        if level == 1:
            title = label
        headings =[(n, h) for n, h in headings if n < level]
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
            raise ValueError(f'条目 {index} 的title必须是非空文字')
        key = str(row.get('id', f'row:{index}'))
        if key in keys:
            raise ValueError(f'重复 id: {key}')
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
                parts.append(f'{name} : {text}')
        if not parts:
            raise ValueError(f'条目{index}没有正文')
        documents.append(make_document(source, title.strip(), '', key, '\n'.join(parts)))
    return documents












