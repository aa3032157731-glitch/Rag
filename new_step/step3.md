# Step 3：将文档切成能用于检索的片段

[上一篇](step2.md) · [教程目录](README.md) · [下一篇](step4.md)

## 本篇目标与前置条件

完成第 2 篇，能读出 5 个文档单元。现在把较长文字切成 `Chunk`，观察长度与重叠。仍然不用模型或数据库。

`Document` 是按文件结构得到的单元；`Chunk` 是实际用于检索的一段文字。短文档可能只产生一个 Chunk，长文档会产生多个。

## 1. 先理解 size 和 overlap

假设一段文字有 1,000 个字符：

- `size=600`：每块最多约 600 个字符，尽量在句末结束。
- `overlap=100`：下一块会重复前一块末尾约 100 个字符。

重叠可以让跨边界的信息在相邻块里仍有上下文，但也会增加重复内容。600/100 是学习起点，不是所有资料的最佳参数。

本篇的字符数使用 Python `len(text)`。它不是模型的 token 数；第 4 篇认识 tokenizer，第 6 篇再处理真正的 token 上限。

## 2. 给配置增加切块参数

将 `backend/app/config.py` 更新为下面的完整状态。本次只是在 Settings 中增加 `chunk_size` 和 `chunk_overlap`；已写过的部分可以保留，对照补齐：

<!-- file: backend/app/config.py -->
```python
from pathlib import Path
import json
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / 'backend' / '.env',
        env_file_encoding='utf-8',
    )
    documents_dir: Path = Path('data/documents')
    chunk_size: int = 600
    chunk_overlap: int = 100


def load_settings():
    settings = Settings()
    settings.documents_dir = (ROOT / settings.documents_dir).resolve()
    return settings


if __name__ == '__main__':
    settings = load_settings()
    print(json.dumps(settings.model_dump(mode='json'), ensure_ascii=False, indent=2))
```

将 `backend/.env.example` 和本机 `backend/.env` 都更新为：

<!-- file: backend/.env.example -->
```dotenv
DOCUMENTS_DIR=data/documents
CHUNK_SIZE=600
CHUNK_OVERLAP=100
```

以后教程说“更新两份配置”，就是同时更新样例和你实际读取的 `.env`；只改样例不会生效。

## 3. 新增 Chunk 结构

更新 `backend/app/models.py`，保留上一篇的定义，在末尾增加 Chunk。完整对照如下：

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
```

`chunk_index` 是该文档内的片段编号；`payload()` 把对象转为普通字典，之后随向量一起存数据库。现在知道它负责保存原文和来源即可。

## 4. 写分段算法

新建 `backend/app/splitter.py`：

<!-- file: backend/app/splitter.py -->
```python
import re

from backend.app.models import Chunk, Document, digest, stable_id


def text_windows(text: str, size: int, overlap: int) -> list[str]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError('需要 size > 0 且 0 <= overlap < size')
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            minimum = max(size // 2, overlap + 1)
            candidates = [
                m.end() for m in re.finditer(r'[\n。！？；.!?;]', text[start:end])
                if m.end() >= minimum
            ]
            if candidates:
                end = start + candidates[-1]
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(text):
            break
        start = end - overlap
    return pieces


def split_document(doc: Document, size=600, overlap=100) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=stable_id(f'{doc.document_id}/{doc.content_hash}/{i}/{digest(text)}'),
            document_id=doc.document_id,
            source=doc.source, title=doc.title, section=doc.section,
            record_key=doc.record_key, chunk_index=i,
            text=text, content_hash=doc.content_hash,
        )
        for i, text in enumerate(text_windows(doc.text, size, overlap))
    ]


def embedding_text(chunk: Chunk) -> str:
    return f'{chunk.title[:80]}\n{chunk.section[:80]}\n{chunk.text}'

if __name__ == '__main__':
    from backend.app.config import load_settings
    from backend.app.loader import load_documents
    settings = load_settings()
    docs = load_documents(settings.documents_dir)
    chunks = [
        chunk for doc in docs
        for chunk in split_document(doc, settings.chunk_size, settings.chunk_overlap)
    ]
    for chunk in chunks:
        print(chunk.source, chunk.record_key, chunk.chunk_index, len(chunk.text))
        print(chunk.text)
        print()
    print('片段数：', len(chunks))
```

按这一轮循环的顺序看：

1. `start` 指向本块开始位置。
2. `end` 先按长度上限计算。
3. 在窗口后半段寻找标点，能找到就缩到最后一个合适标点。
4. 保存文字。
5. 下一块从 `end - overlap` 开始。

`minimum` 还必须大于 overlap，这样下一轮一定向前移动。没有标点时直接按长度切开；这部分保证算法能够完成，并非可以随意去掉的防御代码。

`embedding_text()` 给正文附加少量标题和章节。原始 `chunk.text` 不变，之后界面依然能展示原文。标题只取前 80 个字符，避免长标题占用过多输入。

## 5. 运行并亲眼观察

```powershell
.\.venv\Scripts\python.exe -m backend.app.splitter
```

三份教学资料都比较短，默认通常仍是 5 个片段。这并不表示分段没生效，而是每个文档单元都没有达到 600 字符。

为了看清边界，临时把本机 `.env` 改为：

```dotenv
CHUNK_SIZE=40
CHUNK_OVERLAP=10
```

再次运行，对比每个相邻片段的末尾和开头；观察食堂是否被分成了更多块。实验后恢复 600/100，保持后面章节的基线一致。

## 6. 加两个直接验证算法的小测试

新建 `backend/tests/test_splitter.py`：

<!-- file: backend/tests/test_splitter.py -->
```python
import unittest
from backend.app.splitter import text_windows


class SplitterTests(unittest.TestCase):
    def test_no_text_loss_without_overlap(self):
        text = '甲乙丙丁。' * 50
        pieces = text_windows(text, 31, 0)
        self.assertEqual(''.join(pieces), text)
        self.assertTrue(all(0 < len(p) <= 31 for p in pieces))

    def test_overlap_without_punctuation(self):
        text = ''.join(chr(0x4e00 + i) for i in range(120))
        pieces = text_windows(text, 30, 7)
        restored = pieces[0] + ''.join(p[7:] for p in pieces[1:])
        self.assertEqual(restored, text)
        with self.assertRaises(ValueError):
            text_windows(text, 30, 30)


if __name__ == '__main__':
    unittest.main()
```

执行：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_splitter -v
```

第一项检查无重叠时正文不丢失；第二项检查无标点文本也能向前推进，并验证重叠拼回去仍是原文。片段两端的排版空白会被 strip 去掉，这些测试检查的是有效正文，不是逐字保留所有排版空白。

## 为什么这么设计

整篇文档太长时，一个向量会混合很多主题；切块能让检索更精确地找到局部信息。句末边界帮助保持意思完整，重叠照顾边界附近的上下文。

这一篇只学习字符切分。把 token 二次切分推迟到真正导入模型之前，你会先知道 token 是什么，再理解为什么需要第二次检查，而不用现在背一段暂时没有调用的代码。

## 排查与验收

| 现象 | 检查 |
| --- | --- |
| 提示分段参数不合法 | overlap 必须小于 size，且 size 为正数 |
| 参数没变 | 改的是本机 `.env` 还是样例文件 |
| 片段内容重复 | 检查是否正好是设定的重叠；这通常是预期行为 |
| 一个答案被拆散 | 观察原文和切分位置，先调整 size，而不是增加复杂算法 |

- [ ] 能指出一个 Chunk 的正文、来源与编号。
- [ ] 能解释重叠的用途和代价。
- [ ] 两项测试通过，参数恢复为 600/100。

---

完成验收后进入 [step4](step4.md)。
