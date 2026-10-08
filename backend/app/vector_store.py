from qdrant_client import QdrantClient, models


class VectorStore:
    def __init__(self, settings, client=None, name=None):
        self.name = name or settings.qdrant_collection
        self.client = client or QdrantClient(url=settings.qdrant_url, timeout=15)

    def recreate(self, dimension):
        if self.client.collection_exists(self.name):
            self.client.delete_collection(self.name)
        self.client.create_collection(
            collection_name=self.name,
            vectors_config=models.VectorParams(
                size=dimension,
                distance=models.Distance.COSINE,
            )
        )

    def upsert(self, chunks, vectors):
        if(len(chunks) != len(vectors)):
            raise ValueError('片段数与向量数不同')
        points = [
            models.PointStruct(id=chunk.chunk_id, vector=vector, payload=chunk.payload())
            for chunk, vector in zip(chunks, vectors)
        ]
        self.client.upsert(collection_name=self.name, points=points, wait=True)

    def query(self, vector, limit):
        return self.client.query_points(
            collection_name=self.name,
            query=vector,
            limit=limit,
            with_payload=True,
        ).points

    def count(self):
        return self.client.count(collection_name=self.name, exact=True).count

    def close(self):
        self.client.close()