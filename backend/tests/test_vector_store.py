import unittest
from types import SimpleNamespace

from qdrant_client import QdrantClient

from backend.app.loader import make_document
from backend.app.splitter import split_document
from backend.app.vector_store import VectorStore


def make_chunk(key, text):
    doc = make_document('test.txt', '测试', '', key, text)
    return split_document(doc)[0]


class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        settings = SimpleNamespace(qdrant_collection='test_store')
        self.store = VectorStore(settings, client=QdrantClient(':memory:'))
        self.store.recreate(3)
        self.a = make_chunk('a', '图书馆八点开门')
        self.b = make_chunk('b', '食堂二楼有素食')

    def tearDown(self):
        self.store.close()

    def test_nearest_point_returns_its_text(self):
        self.store.upsert([self.a, self.b], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        rows = self.store.query([0.9, 0.1, 0.0], 1)
        self.assertEqual(rows[0].payload['text'], '图书馆八点开门')
        self.assertEqual(rows[0].payload['source'], 'test.txt')

    def test_same_id_overwrites_instead_of_adding(self):
        self.store.upsert([self.a], [[1.0, 0.0, 0.0]])
        self.store.upsert([self.a], [[1.0, 0.0, 0.0]])
        self.assertEqual(self.store.count(), 1)

    def test_recreate_empties_collection(self):
        self.store.upsert([self.a, self.b], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        self.store.recreate(3)
        self.assertEqual(self.store.count(), 0)

    def test_length_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.upsert([self.a, self.b], [[1.0, 0.0, 0.0]])


if __name__ == '__main__':
    unittest.main()