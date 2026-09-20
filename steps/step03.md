# Step 03：按边界分段，生成稳定片段

## 目标与前置条件

完成 [step02](step02.md)。本篇仍不需要模型或 Docker。我们先按字符数和句子边界切分；模型加载后还会按 token 上限进一步检查。

字符是中文“字”或标点等文本元素；token 是模型分词后的单位，两者不能直接等同。

## 1. 新建 `backend/app/splitter.py`

我们用可前进的滑动窗口保证任何长文本都能切开，再尽量选择换行或句末。连续片段的重叠只在同一个 Document 内发生，不跨 JSON 条目。

<!-- file: backend/app/splitter.py -->
```python
from dataclasses import replace
import re

from backend.app.models import Chunk, Document, digest, stable_id


def text_windows(text: str, size: int, overlap: int) -> list[str]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError('分段参数不合法')
    output = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # 只在窗口后半段找边界；边界还必须越过 overlap。
            minimum = start + max(size // 2, overlap + 1)
            candidates = [
                start + m.end()
                for m in re.finditer(r'[\n。！？；.!?;]', text[start:end])
                if start + m.end() >= minimum
            ]
            if candidates:
                end = candidates[-1]
        piece = text[start:end].strip()
        if piece:
            output.append(piece)
        if end == len(text):
            break
        start = end - overlap
    return output


def split_document(doc: Document, size=600, overlap=100) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=stable_id(f'{doc.document_id}/{doc.content_hash}/{i}/{digest(text)}'),
            document_id=doc.document_id, source=doc.source,
            title=doc.title, section=doc.section, record_key=doc.record_key,
            chunk_index=i, text=text, content_hash=doc.content_hash,
        )
        for i, text in enumerate(text_windows(doc.text, size, overlap))
    ]


def embedding_text(chunk: Chunk) -> str:
    # 限制标题长度，防止标题占满 token 预算。
    return f'{chunk.title[:80]}\n{chunk.section[:80]}\n{chunk.text}'


def fit_token_budget(chunks, count_tokens, limit):
    """count_tokens 是可注入的函数：测试可用 len，实际传 tokenizer。"""
    result = []
    indexes = {}
    for original in chunks:
        pending = [original.text]
        accepted = []
        while pending:
            text = pending.pop()
            trial = replace(original, text=text)
            if count_tokens(embedding_text(trial)) <= limit:
                accepted.append(text)
                continue
            if len(text) <= 1:
                raise ValueError(f'标题或单字符也超过 token 预算：{original.source}')
            middle = len(text) // 2
            # 栈后进先出：先放右半，保证左半先处理。
            pending.extend([text[middle:], text[:middle]])
        for text in accepted:
            index = indexes.get(original.document_id, 0)
            indexes[original.document_id] = index + 1
            result.append(replace(
                original, text=text, chunk_index=index,
                chunk_id=stable_id(f'{original.chunk_id}/token/{index}/{digest(text)}'),
            ))
    return result


if __name__ == '__main__':
    from backend.app.config import load_settings
    from backend.app.loader import load_documents
    settings = load_settings()
    report = load_documents(settings.documents_dir)
    if report.errors:
        raise SystemExit('\n'.join(report.errors))
    chunks = [c for doc in report.documents for c in split_document(
        doc, settings.chunk_size, settings.chunk_overlap
    )]
    for chunk in chunks:
        print(chunk.chunk_id, chunk.source, chunk.section)
        print(chunk.text)
    print('字符分段数量：', len(chunks))
```

### 关键逻辑

- 当窗口内没有合适标点时，仍然按 `size` 切开，不会一直等待标点。
- `minimum` 大于 overlap，保证下一次 `start` 比这次大，避免死循环。
- `replace()` 复制不可变 dataclass，只替换指定字段，保留来源。
- `fit_token_budget` 暂不在命令行示例调用；到 step04 得到 tokenizer 后使用。它继续拆分超长段，不静默截掉末尾。
- token 二次切分优先保证不丢字，不增加额外重叠；字符分段已经保留了重叠。特别长段落应通过评估判断是否还需要优化语义边界。

## 2. 新建测试 `backend/tests/test_splitter.py`

<!-- file: backend/tests/test_splitter.py -->
```python
import unittest

from backend.app.loader import make_document
from backend.app.splitter import (
    text_windows, split_document, fit_token_budget, embedding_text,
)


class SplitterTests(unittest.TestCase):
    def test_no_loss_without_overlap(self):
        text = '甲乙丙丁。' * 50
        pieces = text_windows(text, 31, 0)
        self.assertEqual(''.join(pieces), text)
        self.assertTrue(all(0 < len(p) <= 31 for p in pieces))

    def test_overlap_and_progress_without_punctuation(self):
        text = ''.join(chr(0x4e00 + i) for i in range(120))
        pieces = text_windows(text, 30, 7)
        merged = pieces[0] + ''.join(p[7:] for p in pieces[1:])
        self.assertEqual(merged, text)
        with self.assertRaises(ValueError):
            text_windows(text, 30, 30)

    def test_stable_ids_and_business_isolation(self):
        a = make_document('x.json', '甲', '', 'a', '甲资料。' * 20)
        b = make_document('x.json', '乙', '', 'b', '乙资料。' * 20)
        left = split_document(a, 20, 3)
        right = split_document(b, 20, 3)
        self.assertEqual(left, split_document(a, 20, 3))
        self.assertTrue(set(c.chunk_id for c in left).isdisjoint(c.chunk_id for c in right))
        self.assertTrue(all('乙' not in c.text for c in left))

    def test_token_refinement_preserves_order_and_text(self):
        doc = make_document('x.txt', '短标题', '', 'x', '甲乙丙丁' * 50)
        original = split_document(doc, 500, 0)
        refined = fit_token_budget(original, len, 40)
        self.assertEqual(''.join(c.text for c in refined), doc.text)
        self.assertTrue(all(len(embedding_text(c)) <= 40 for c in refined))
        self.assertEqual(len(set(c.chunk_id for c in refined)), len(refined))


if __name__ == '__main__':
    unittest.main()
```

## 3. 执行与人工检查

终端 A，根目录：

```powershell
.\.venv\Scripts\python.exe -m backend.app.splitter
.\.venv\Scripts\python.exe -m unittest backend.tests.test_splitter -v
```

目前样例都很短，通常还是 5 个片段。这是正确的，不要为了“看到很多片段”强行把 600 改成极小数字。测试代码已覆盖长文本。

人工检查“校园卡补办”片段：名称、材料、地点、时间是否在一起。思考用户问“补卡带什么”时，这一段能不能独立支持答案。

## 4. 排查与验收

| 现象 | 原因与解决 |
| --- | --- |
| 程序卡住 | 检查是否照抄了 `minimum` 与 `start = end - overlap`；确认 overlap 小于 size |
| 片段重复一些文字 | 相邻重叠是设计行为；不同业务条目不应互相混入 |
| 分段后尾部丢失 | 检查循环末尾是否处理到 `len(text)`；运行无丢字测试 |
| ID 每次变 | 不要把 `uuid5` 换成每次随机的 `uuid4` |

- [ ] 4 个测试通过。
- [ ] 同一输入重复执行，片段和 ID 一致。
- [ ] 每段都有文件来源，样例业务信息完整。

下一篇：[step04：本机运行 BGE-M3](step04.md)。
