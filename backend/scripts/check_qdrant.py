import argparse
from uuid import uuid4
from qdrant_client import models

from backend.app.config import load_settings
from backend.app.vector_store import VectorStore

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--name',
        help='重启后查询上次创建的测试 collection'
    )
    args = parser.parse_args()
    store = VectorStore(load_settings())
    name = args.name or 'tutorial_' + uuid4().hex[:12]
    try:
        if not args.name:
            store.create(name, 3)
        store.client.upsert(name, points=[
            models.PointStruct(
                id=1,
                vector=[1.0, 0.0, 0.0],
                payload={'text': '甲'}
            ),
            models.PointStruct(
                id=2,
                vector=[0.0, 1.0, 0.0],
                payload={'text': '乙'}
            ),
        ], wait=True)
        rows = store.query(name, [1.0, 0.0, 0.0], 1)
        assert rows and rows[0].payload['text'] == '甲'
        print('验证通过, collection', name)
        print('重启后运行: python -m backend.scripts.check_qdrant --name',
              name)
    finally:
        store.close()

if __name__ == '__main__':
    main()
