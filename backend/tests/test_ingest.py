import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from qdrant_client import QdrantClient

from backend.app.vector_store import VectorStore
from backend.ingest import load_chunks, ingest


class FakeModel:
    """假模型：不加载 BGE-M3，用字符数代替 token 数，所有向量都一样。"""
    dimension = 3

    def count_tokens(self, text):
        return len(text)

    def encode_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


class BrokenModel(FakeModel):
    def encode_documents(self, texts):
        raise RuntimeError('模拟编码失败')


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = SimpleNamespace(
            documents_dir=self.root, chunk_size=600, chunk_overlap=100,
            qdrant_collection='test_ingest',
        )
        self.store = VectorStore(self.settings, client=QdrantClient(':memory:'))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def write(self, name, text):
        (self.root / name).write_text(text, encoding='utf-8')

    def test_running_twice_does_not_double(self):
        self.write('a.txt', '图书馆八点开门。')
        self.write('b.txt', '食堂二楼有素食。')
        chunks = load_chunks(self.settings)
        self.assertEqual(ingest(chunks, FakeModel(), self.store, 1024), 2)
        self.assertEqual(ingest(chunks, FakeModel(), self.store, 1024), 2)

    def test_deleted_file_disappears_after_ingest(self):
        self.write('a.txt', '图书馆八点开门。')
        self.write('b.txt', '食堂二楼有素食。')
        ingest(load_chunks(self.settings), FakeModel(), self.store, 1024)
        (self.root / 'b.txt').unlink()
        self.assertEqual(ingest(load_chunks(self.settings), FakeModel(), self.store, 1024), 1)

    def test_bad_document_stops_before_model(self):
        self.write('bad.json', '{')
        with self.assertRaises(ValueError):
            load_chunks(self.settings)

    def test_encode_failure_keeps_old_data(self):
        self.write('a.txt', '图书馆八点开门。')
        chunks = load_chunks(self.settings)
        ingest(chunks, FakeModel(), self.store, 1024)
        with self.assertRaises(RuntimeError):
            ingest(chunks, BrokenModel(), self.store, 1024)
        self.assertEqual(self.store.count(), 1)

    def test_long_text_is_split_by_token_budget(self):
        self.write('long.txt', '甲乙丙丁' * 50)
        count = ingest(load_chunks(self.settings), FakeModel(), self.store, 64)
        self.assertGreater(count, 1)


if __name__ == '__main__':
    unittest.main()