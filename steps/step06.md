# Step 06：导入资料，安全切换知识库版本

> **本节改动说明**：`import_lock` 冲突时的报错信息现在会附带锁文件里记录的 PID，你不用再自己打开文件去看是哪个进程占着锁。其余代码与原教程一致；文档结构做了统一整理。

## 本节目标

完成后你能：把 `data/documents` 里的资料一键构建成新的 Qdrant collection，校验通过后再切换 `rag_active`，并且任何一步失败都不会影响正在使用的旧索引。

**前置条件**：完成 step01～05，Qdrant 正在运行，BGE-M3 已经完成过一次下载。开始前先退出其他会加载模型的 Python 进程（CLI、FastAPI 都不要同时开着）。

## 核心概念：为什么是"全量重建 + 切换"而不是"原地更新"

资料量小的时候，原地增量更新（只改动几条记录）看起来更省事，但要正确处理"删除""重命名""分段参数变化"这些情况会复杂很多，还容易出现"数据库里有旧片段但源文件已经不在了"的脏数据。

所以第一版的策略更简单也更安全：

```
新建一个空 collection → 写入本次全部数据 → 校验数量和抽样查询 → 写 manifest → 切换 alias
```

任何一步失败，`rag_active` 都还指着上一次成功的版本。旧 collection 默认不删除，所以理论上你可以手动切回去。

## 一、新建 `backend/ingest.py`

<!-- file: backend/ingest.py -->
```python
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from backend.app.config import ROOT, load_settings
from backend.app.loader import load_documents
from backend.app.splitter import split_document, fit_token_budget, embedding_text


@contextmanager
def import_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'ingest.lock'
    try:
        handle = path.open('x', encoding='utf-8')
    except FileExistsError as exc:
        # 把锁文件里记录的 PID 一并报出来，省得你还要自己打开文件确认是哪个进程。
        stale_pid = path.read_text(encoding='utf-8').strip() if path.exists() else '未知'
        raise RuntimeError(
            f'已有导入锁：{path}（记录的进程 PID：{stale_pid}）；'
            '确认该进程已经结束、没有其他导入在运行后，才能手工删除这个文件'
        ) from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def publish_index(settings, report, chunks, model, store):
    # 参数全部可注入，测试不需要真的加载 BGE-M3。
    chunks = fit_token_budget(chunks, model.count_tokens, settings.max_tokens)
    if not chunks:
        raise ValueError('没有片段，不发布空知识库')
    name = 'rag_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_') + uuid4().hex[:8]
    store.create(name, model.dimension)
    first_vector = None
    for start in range(0, len(chunks), settings.batch_size):
        batch = chunks[start:start + settings.batch_size]
        vectors = model.encode_documents([embedding_text(c) for c in batch])
        if first_vector is None:
            first_vector = vectors[0]
        store.upsert(name, batch, vectors)
        print(f'已写入 {min(start + len(batch), len(chunks))}/{len(chunks)}')
    if store.count(name) != len(chunks):
        raise RuntimeError('写入计数不一致，拒绝发布')
    if not store.query(name, first_vector, 1):
        raise RuntimeError('索引抽样查询失败，拒绝发布')
    manifest = {
        'collection': name,
        'dimension': model.dimension,
        'fingerprint': model.fingerprint(),
        'model': settings.model_name,
        'revision': model.revision,
        'chunk_size': settings.chunk_size,
        'chunk_overlap': settings.chunk_overlap,
        'max_tokens': settings.max_tokens,
        'files': report.files,
        'document_count': len(report.documents),
        'chunk_count': len(chunks),
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    directory = store.manifest_dir
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f'{name}.json.tmp'
    final = directory / f'{name}.json'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(final)
    # 必须最后才切换 alias：此前任何异常都会让旧 alias 保持原样。
    store.switch_alias(name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description='文档预检查与索引重建')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--rebuild', action='store_true')
    args = parser.parse_args()
    settings = load_settings()
    report = load_documents(settings.documents_dir)
    print('文件数：', len(report.files), '文档单元：', len(report.documents))
    for item in report.skipped:
        print('跳过：', item)
    if report.errors:
        raise SystemExit('导入中止：\n' + '\n'.join(report.errors))
    chunks = [c for doc in report.documents for c in split_document(
        doc, settings.chunk_size, settings.chunk_overlap
    )]
    if not chunks:
        raise SystemExit('目录为空或没有可导入片段，旧索引保持不变')
    print('字符分段数：', len(chunks))
    if args.dry_run:
        print('预检查结束：未加载模型，未修改数据库；尚未检查 token 长度')
        return
    # 重型依赖只在确实要导入时才加载，dry-run 不应该等模型下载。
    from backend.app.embedding import EmbeddingService
    from backend.app.vector_store import VectorStore
    with import_lock(ROOT / 'data' / 'manifests'):
        model = EmbeddingService(settings)
        store = VectorStore(settings)
        try:
            result = publish_index(settings, report, chunks, model, store)
            print('活动索引已切换：', result['collection'])
            print('片段数：', result['chunk_count'])
        finally:
            store.close()


if __name__ == '__main__':
    main()
```

## 二、关键代码解读

- **`open('x')` 就是文件锁**：`'x'` 模式要求文件必须不存在才能创建，这是一种简单但可靠的互斥手段，避免两次导入同时争抢 alias。锁文件里写了 PID，配合上面提到的改动，卡住时你能立刻知道是哪个进程。
- **`--dry-run` 完全不碰模型和数据库**：只做加载、分段，用来快速检查资料格式对不对。注意它**不会**检查 token 长度（那一步在 `publish_index` 里通过 `fit_token_budget` 完成），所以预检查的片段数和最终实际写入的数量可能不完全一样。
- **写 manifest 用"先写临时文件再 rename"的模式**：`temporary.write_text(...)` 之后 `temporary.replace(final)`，这样即使写到一半进程被杀掉，也不会留下一个内容残缺的 `.json` 文件被后续读到。
- **`switch_alias` 永远是最后一步**：前面任何异常（写入失败、计数不对、抽样查询失败）都会在切换 alias 之前抛出，`rag_active` 因此始终指向上一个"确认可用"的版本。
- **失败后的 collection 不会自动清理**：这是有意为之，方便你排错和回滚；第一版没有自动垃圾回收机制。

## 三、新建离线发布测试

文件：`backend/tests/test_ingest.py`。这里用模拟模型和数据库，验证的是"失败不会切换 alias"这个安全属性，而不是真实的向量效果。

<!-- file: backend/tests/test_ingest.py -->
```python
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from backend.app.loader import LoadReport, make_document
from backend.app.splitter import split_document
from backend.ingest import publish_index, import_lock


class FakeModel:
    dimension = 3
    revision = 'test'
    def count_tokens(self, text):
        return len(text)
    def encode_documents(self, texts):
        return [[1.0, 0.0, 0.0] for text in texts]
    def fingerprint(self):
        return 'test-fingerprint'


class FakeStore:
    def __init__(self, directory, fail=False):
        self.manifest_dir = Path(directory)
        self.rows = []
        self.alias = 'old'
        self.fail = fail
    def create(self, name, dimension):
        self.rows = []
    def upsert(self, name, chunks, vectors):
        if self.fail:
            raise RuntimeError('模拟写入失败')
        self.rows.extend(chunks)
    def count(self, name):
        return len(self.rows)
    def query(self, name, vector, limit):
        return self.rows[:limit]
    def switch_alias(self, name):
        self.alias = name


class IngestTests(unittest.TestCase):
    def test_publish_and_failure(self):
        settings = SimpleNamespace(
            max_tokens=1024, batch_size=1, model_name='fake',
            chunk_size=600, chunk_overlap=100,
        )
        doc = make_document('x.txt', '标题', '', 'x', '资料正文')
        report = LoadReport(documents=[doc], files={'x.txt': 'hash'})
        chunks = split_document(doc)
        with tempfile.TemporaryDirectory() as temp:
            store = FakeStore(temp)
            manifest = publish_index(settings, report, chunks, FakeModel(), store)
            self.assertEqual(store.alias, manifest['collection'])
            self.assertTrue((Path(temp) / (store.alias + '.json')).exists())
            failing = FakeStore(temp, fail=True)
            with self.assertRaises(RuntimeError):
                publish_index(settings, report, chunks, FakeModel(), failing)
            self.assertEqual(failing.alias, 'old')

    def test_lock_rejects_second_writer_and_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with import_lock(root):
                with self.assertRaises(RuntimeError):
                    with import_lock(root):
                        self.fail('不应该取得第二把锁')
            self.assertFalse((root / 'ingest.lock').exists())


if __name__ == '__main__':
    unittest.main()
```

## 四、首次导入

终端 A，项目根目录；确保 Docker 在运行，其他模型进程已退出：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_ingest -v
.\.venv\Scripts\python.exe -m backend.ingest --dry-run
.\.venv\Scripts\python.exe -m backend.ingest --rebuild
```

应该能看到写入进度、切换后的活动索引名字和片段数量。打开 `data/manifests` 检查对应的 JSON 文件，确认里面的文件列表就是你的合成资料。

```powershell
Invoke-RestMethod 'http://127.0.0.1:6333/aliases'
```

响应里应该能看到 `rag_active`，指向刚才打印出来的那个 collection。

## 五、更新、删除与失败场景验证

按顺序动手验证，能帮你在早期就摸清系统的边界行为：

1. 再执行一次 `--rebuild`。新 collection 的名字会变，但活动索引里的片段数不应该翻倍。
2. 临时把图书馆关门时间改成 21:00，重建后 step07 查询应该只看到新版本。验证完记得改回 22:00 并重建，保持后面几篇的示例一致。
3. 把 `canteen.txt` 临时移出 `data/documents`，重建后新的 manifest 不应该包含它。验证完移回并重建。
4. 在资料目录里临时放一个语法错误的 JSON 文件，跑 `--dry-run` 必须报错，`rag_active` 应该保持不变。测试完删掉这个坏文件。
5. 不要靠删除 Docker 卷来"重新开始"——更新资料根本不需要清空整个数据库。

## 六、排查与验收

| 现象 | 原因与处理 |
| --- | --- |
| 提示已有 `ingest.lock` | 正常结束的导入会自动移除锁；如果是异常崩溃后残留，先按报错里的 PID 确认那个进程真的已经结束，再手动删除锁文件 |
| alias 没有变化 | 看清楚错误日志——失败时保留旧索引正是设计意图，不是 bug |
| collection 越攒越多 | 每次重建都保留旧版本；确认不再需要的版本后可以自己写一段脚本调用 `delete_inactive` 清理，教程不做自动批量删除 |
| 写入计数对不上 | 检查是否有重复的 `chunk_id`，或者写入过程中途报错但没有被正确捕获 |

- [ ] `--dry-run` 不下载模型、不修改数据库。
- [ ] 正式导入之后出现活动 alias 和对应的 manifest 文件。
- [ ] 重复导入不会让活动索引的片段数累加。
- [ ] 离线失败测试通过；资料已经恢复到教程基线状态。

下一篇：[step07：在命令行提问](step07.md)。
