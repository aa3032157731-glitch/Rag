# Step 06：导入资料，安全切换知识库版本

## 目标与前置条件

完成 step01～05，Qdrant 正在运行，BGE-M3 已完成首次下载。停止其他 Python 模型进程，不同时开启 FastAPI。

第一版采用全量重建：在新 collection 构建 → 检查 → 写 manifest → 切换 alias。原来的 collection 保留，所以新导入失败不会让原知识库立即消失。

## 1. 新建 `backend/ingest.py`

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
        raise RuntimeError(f'已有导入锁：{path}；确认没有导入进程后才能手工移除') from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def publish_index(settings, report, chunks, model, store):
    # 参数可注入，测试不需要真的加载 BGE-M3。
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
    # 必须最后切换。此前任何异常都让旧 alias 保持原样。
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
    # 重型依赖只在确实导入时加载。
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

### 关键解释

- `open('x')` 以“文件必须不存在”的方式取得导入锁，避免两次导入同时争抢 alias。
- `--dry-run` 不构造模型也不连接数据库，适合每次手改资料后快速检查格式。
- token 二次分段会改变最终数量，因此预检查片段数与最终数不一定相同。
- manifest 与具体 collection 同名；先写临时文件再 replace，防止读到一半的 JSON。
- alias 更新响应若因网络中断而不明确，先查询实际 alias，不能直接判断一定成功或失败。数据库原子更新保证不会出现更新一半的 alias，但网络响应仍可能丢失。
- 失败 collection 可能保留在数据库里，但没有活动 alias，检索不会读它。第一版不自动清理，以便排错与回滚。

## 2. 新建离线发布测试

文件：`backend/tests/test_ingest.py`。用模拟模型和数据库验证“失败不切换”，不测真实向量效果。

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

## 3. 首次导入

终端 A，根目录；确保 Docker 正在运行，其他模型进程已退出：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_ingest -v
.\.venv\Scripts\python.exe -m backend.ingest --dry-run
.\.venv\Scripts\python.exe -m backend.ingest --rebuild
```

应看到写入进度、活动索引版本和片段数。打开 `data/manifests` 检查对应 JSON，确认文件列表是你的合成资料。

```powershell
Invoke-RestMethod 'http://127.0.0.1:6333/aliases'
```

响应应包含 `rag_active`，指向本次打印的 collection。

## 4. 更新、删除与失败验证

1. 再次执行 `--rebuild`。新 collection 名会变，但活动索引的片段数不应翻倍。
2. 临时把图书馆关门时间改为 21:00，重建；step07 查询时应仅看到新版本。验证后恢复 22:00 并重建，以保持后续样例一致。
3. 把 `canteen.txt` 移到 `data/documents` 以外临时保存，重建；新 manifest 不应包含它。验证后移回并重建。
4. 在资料目录临时放一个语法错误的 JSON，运行 dry-run，必须报错。alias 应保持不变。删除这个你自己创建的测试坏文件。
5. 不要通过删除 Docker 卷来“重新开始”；资料更新不需要清空整个数据库。

## 5. 排查与验收

| 现象 | 原因与处理 |
| --- | --- |
| 有 ingest.lock | 正常导入结束会移除；崩溃后先确认没有导入进程，再只移除该锁文件 |
| alias 没变化 | 读错误日志；失败保留旧索引正是设计行为 |
| collection 越来越多 | 每次重建保留旧版本；确认不用的版本后可另写显式清理，不在此处批量删 |
| 计数不等 | 检查 chunk_id 是否重复，或写入是否出错 |

- [ ] dry-run 不下载模型、不修改数据库。
- [ ] 正式导入后出现活动 alias 和 manifest。
- [ ] 重复导入不使活动索引重复累加。
- [ ] 离线失败测试通过；资料恢复到教程基线。

下一篇：[step07：在命令行提问](step07.md)。
