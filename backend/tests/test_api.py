import unittest

from fastapi.testclient import TestClient
from qdrant_client.http.exceptions import ResponseHandlingException

from backend.api import create_app
from backend.app.models import Hit


class FakeRetriever:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def search(self, question, top_k=None):
        self.calls.append((question, top_k))
        if self.error:
            raise self.error
        if not question.strip():
            raise ValueError('问题不能为空')
        return [Hit('id1', 'campus/library.md', '图书馆', '开放时间', 'md:0', '每天 8:00 开门。', 0.66)]


class ApiTests(unittest.TestCase):
    def client(self, retriever):
        return TestClient(create_app(retriever))

    def test_retrieve_returns_hits(self):
        fake = FakeRetriever()
        with self.client(fake) as client:
            response = client.post('/retrieve', json={'question': '图书馆几点开门', 'top_k': 3})
        self.assertEqual(response.status_code, 200)
        hit = response.json()['hits'][0]
        self.assertEqual(hit['source'], 'campus/library.md')
        self.assertEqual(hit['text'], '每天 8:00 开门。')
        self.assertEqual(fake.calls, [('图书馆几点开门', 3)])

    def test_bad_requests_are_422(self):
        with self.client(FakeRetriever()) as client:
            self.assertEqual(client.post('/retrieve', json={'question': ''}).status_code, 422)
            self.assertEqual(client.post('/retrieve', json={'question': '   '}).status_code, 422)
            self.assertEqual(client.post('/retrieve', json={'question': '你好', 'top_k': 0}).status_code, 422)
            self.assertEqual(client.post('/retrieve', json={}).status_code, 422)

    def test_database_down_is_503(self):
        fake = FakeRetriever(error=ResponseHandlingException(ConnectionError('refused')))
        with self.client(fake) as client:
            response = client.post('/retrieve', json={'question': '图书馆'})
        self.assertEqual(response.status_code, 503)

    def test_health(self):
        with self.client(FakeRetriever()) as client:
            self.assertEqual(client.get('/health').json(), {'status': 'ok'})


if __name__ == '__main__':
    unittest.main()