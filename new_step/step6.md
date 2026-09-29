# Step 6：把文档、分段、向量和数据库串起来

[上一篇](step5.md) · [教程目录](README.md) · [下一篇](step7.md)

## 本篇目标与前置条件

前面四个模块已经各自运行过：读取、分段、模型、数据库。现在把它们串联成导入流程。

先确认 Qdrant 正在运行，并退出其他加载模型的 Python 程序。正式导入集合固定为 `rag_tutorial`；更新时暂停 CLI、API 和 Unity 查询。

本篇流程：

```text
读取所有资料
  → 按字符分段
  → 按模型 token 上限继续细分
  → 编码得到向量
  → 重建 rag_tutorial
  → 写入向量和原文
```

这里会重建指定的正式集合。源文档始终保留；已经存在的该集合索引数据会被替换。其他集合不处理。

## 1. 为什么字符切分后还要看 token

`len(text)` 数的是字符，模型的 tokenizer 使用另一套切分单位。同样 600 个字符，中文、英文、数字和符号的 token 数可能不同。编码输入还包含标题和章节，也要算进去。

第 4 篇的编码器已经会拒绝超长输入。这一篇补上处理办法：如果太长就继续拆分正文，直到每一段满足模型预算。不能直接丢掉后半段，因为答案可能正好在那里。

## 2. 更新 splitter，加入真正需要的 token 处理

更新 `backend/app/splitter.py`。已有的字符切分保持原样；顶部新增 `replace`，新增 `fit_token_budget()`。下面给出完整文件，便于检查插入位置：

<!-- file: backend/app/splitter.py -->
```python
from dataclasses import replace
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

def fit_token_budget(chunks, count_tokens, limit):
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
                raise ValueError(f'标题或最短正文仍超出 token 预算：{original.source}')
            middle = len(text) // 2
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

先理解 `fit_token_budget()` 中的三个容器：

- `pending`：还需要检查的正文。`pop()` 每次取最后一个。
- `accepted`：已经满足预算的正文。
- `result`：重新建立标识和编号后的最终片段。

超长时切成左右两半，把右边先放入、左边后放入；由于最后放入的先取出，原文顺序得到保留。`replace()` 复制原片段并更新少量字段，来源信息不会丢掉。

这里不额外增加重叠，初次字符切分已经做过重叠。二次切分优先保证正文完整、顺序正确，后面再通过实际问题观察片段是否适合检索。

## 3. 写一个只有主线的导入入口

新建 `backend/ingest.py`：

<!-- file: backend/ingest.py -->
```python
from backend.app.config import load_settings
from backend.app.loader import load_documents
from backend.app.splitter import split_document, embedding_text, fit_token_budget


def main():
    settings = load_settings()
    docs = load_documents(settings.documents_dir)
    if not docs:
        raise ValueError('没有资料，请先准备 data/documents 中的文件')
    chunks = [
        chunk for doc in docs
        for chunk in split_document(doc, settings.chunk_size, settings.chunk_overlap)
    ]
    print('文档单元：', len(docs), '字符分段：', len(chunks))

    from backend.app.embedding import EmbeddingService
    from backend.app.vector_store import VectorStore
    model = EmbeddingService(settings)
    chunks = fit_token_budget(chunks, model.count_tokens, settings.max_tokens)
    vectors = model.encode_documents([embedding_text(chunk) for chunk in chunks])
    print('完成编码，准备重建集合：', settings.qdrant_collection)

    store = VectorStore(settings)
    try:
        store.rebuild(model.dimension)
        store.upsert(chunks, vectors)
        print('导入完成，片段数：', store.count())
    finally:
        store.close()


if __name__ == '__main__':
    main()
```

这段代码按从上到下的顺序运行：

1. 先读资料并完成字符分段，常见的空目录和 JSON 格式问题先暴露。
2. 再加载模型，按 token 细分并生成向量。
3. 编码成功后才连接并重建目标集合。
4. 等待写入完成，打印条数，关闭客户端。

`batch_size` 已由 EmbeddingService 传给模型，模型内部会分批编码。教学样本很少，这里一次收集全部片段和向量，避免再写一套批量调度。大规模资料的流式导入属于后续扩展。

## 4. 第一次导入

终端在根目录运行：

```powershell
docker compose -f infra/compose.yaml up -d
.\.venv\Scripts\python.exe -m backend.ingest
```

预期看到文档单元数、分段数、准备重建的集合名和最终条数。只有教程三份资料且参数恢复为 600/100 时，通常是 5 个文档单元、5 个最终片段；实际条数还取决于你的输入和参数。

完成后可查看数据库中的条数：

```powershell
Invoke-RestMethod 'http://127.0.0.1:6333/collections/rag_tutorial'
```

响应较长，找到 `points_count`。现在已经有一个可以查询的索引，下一篇就会拿问题来查询它。

## 5. 用小实验理解“全量重建”

依次做下面几件事，每次都停止查询程序再导入：

1. 不改资料，再运行一次 `backend.ingest`。条数不应翻倍。
2. 把图书馆 22:00 改为 21:00，再导入。下一篇查询时应看到新原文。
3. 暂时把食堂文件移到 `data/documents` 外，再导入，条数应减少。
4. 实验结束后恢复三份原始资料和 22:00，再导入，供后续章节统一对照。

删除资料后仅运行查询是不够的：数据库保存的是上一次导入结果，必须重建才能反映变化。

如果写入途中失败，修复原因后重新运行导入即可。这个学习版本没有版本切换和回滚，不承诺失败后旧索引仍完整可用。

## 6. 运行成功后验证二次分段

新建 `backend/tests/test_token_budget.py`：

<!-- file: backend/tests/test_token_budget.py -->
```python
import unittest
from backend.app.loader import make_document
from backend.app.splitter import split_document, embedding_text, fit_token_budget


class TokenBudgetTests(unittest.TestCase):
    def test_refinement_preserves_text_and_order(self):
        doc = make_document('example.txt', '标题', '', 'text', '甲乙丙丁' * 50)
        original = split_document(doc, 500, 0)
        refined = fit_token_budget(original, len, 40)
        self.assertEqual(''.join(chunk.text for chunk in refined), doc.text)
        self.assertTrue(all(len(embedding_text(chunk)) <= 40 for chunk in refined))
        self.assertEqual(len({chunk.chunk_id for chunk in refined}), len(refined))


if __name__ == '__main__':
    unittest.main()
```

运行：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_token_budget -v
```

这里把 `len` 当作便于观察的计数函数，只验证“继续拆分、不丢字、保持顺序”的算法。它不声称字符数等于真实 token；正式导入传入的是 `model.count_tokens`。

## 为什么这么设计

导入阶段负责把原始资料变成可查询的数据，查询阶段只负责使用它。将这两件事分开，后面每次提问就不需要重读全部文件、重新编码全部文档。

全量重建让新增、修改和删除资料都通过同一条路径生效。对本机小样本项目，固定集合加手动更新比索引版本系统更容易理解；真正需要保护的输入长度和资料完整性仍然保留。

## 排查与验收

| 现象 | 检查 |
| --- | --- |
| 文档目录为空 | 第 2 篇资料是否写在正确路径 |
| token 最短正文仍超限 | 标题、章节是否过长，或 MAX_TOKENS 是否过小 |
| 数据库连不上 | Qdrant 容器与本机地址 |
| 更新后看到旧信息 | 是否成功重新导入，是否查询同一集合 |
| 导入没有正常结束 | 修复错误后重新导入，不直接开始查询半成品集合 |

- [ ] 导入流程能完成并打印条数。
- [ ] 重复导入不使记录累加。
- [ ] 明白字符数与 token 数不同。
- [ ] 资料已经恢复，二次分段测试通过。

---

下一篇：[step7：开始提问并找到原文](step7.md)。
