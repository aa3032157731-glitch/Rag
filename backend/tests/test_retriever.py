import unittest
from types import SimpleNamespace

from qdrant_client import QdrantClient

from backend.app.loader import make_document
from backend.app.retriever import Retriever
from backend.app.splitter import split_document
from backend.app.vector_store import VectorStore


class FakeModel:
    """假模型：问题里出现哪个关键词，就返回对应的方向向量。"""
    directions = {'图书馆': [1.0, 0.0, 0.0], '食堂': [0.0, 1.0, 0.0]}

    def encode_query(self, question):
        for word, vector in self.directions.items():
            if word in question:
                return vector
        return [0.0, 0.0, 1.0]


class RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.store = VectorStore(SimpleNamespace(qdrant_collection='test_retriever'),
                                 client=QdrantClient(':memory:'))
        self.store.recreate(3)
        texts = ['图书馆八点开门。', '图书馆在明德楼东侧。', '食堂二楼有素食。']
        vectors = [[1.0, 0.0, 0.0], [0.8, 0.6, 0.0], [0.0, 1.0, 0.0]]
        chunks = [split_document(make_document('t.txt', '测试', '', str(i), t))[0]
                  for i, t in enumerate(texts)]
        self.store.upsert(chunks, vectors)

    def tearDown(self):
        self.store.close()

    def test_results_sorted_by_score(self):
        hits = Retriever(FakeModel(), self.store).search('图书馆几点开门')
        self.assertEqual(hits[0].text, '图书馆八点开门。')
        self.assertEqual([h.score for h in hits], sorted([h.score for h in hits], reverse=True))

    def test_top_k_limits_count(self):
        hits = Retriever(FakeModel(), self.store, top_k=2).search('图书馆')
        self.assertEqual(len(hits), 2)
        self.assertEqual(len(Retriever(FakeModel(), self.store).search('图书馆', top_k=1)), 1)

    def test_min_score_drops_weak_hits(self):
        hits = Retriever(FakeModel(), self.store, min_score=0.5).search('图书馆')
        self.assertEqual([h.text for h in hits], ['图书馆八点开门。', '图书馆在明德楼东侧。'])
        self.assertEqual(Retriever(FakeModel(), self.store, min_score=0.5).search('天气'), [])

    def test_budget_keeps_whole_chunks(self):
        hits = Retriever(FakeModel(), self.store, max_context_chars=12).search('图书馆')
        self.assertEqual([h.text for h in hits], ['图书馆八点开门。'])

    def test_empty_question_rejected(self):
        with self.assertRaises(ValueError):
            Retriever(FakeModel(), self.store).search('   ')


if __name__ == '__main__':
    unittest.main()