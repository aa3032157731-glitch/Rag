import json
from pathlib import Path

from qdrant_client import QdrantClient, models

from backend.app.config import ROOT, Settings
from backend.app.models import ServiceUnavailable, Chunk


class VectorStore:
    def __init__(self, settings, client=None, manifest_dir=None):
        self.settings = settings
        self.client = client or QdrantClient(
            url=settings.qdrant_url,
            timeout=15,
        )
        self.manifest_dir = Path(
            manifest_dir or ROOT / 'data' / 'manifests'
        )

    def close(self):
        self.client.close()

    def create(self, name, dimension):
        self.client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=dimension,
                distance=models.Distance.COSINE,
            ),
        )

    def upsert(self, name, chunks, vectors):
        if len(chunks) != len(vectors):
            raise ValueError('片段数与向量数不同')
        points = [models.PointStruct(
            id=chunk.chunk_id,
            vector=vector,
            payload={**chunk.payload(), 'index_version': name}
        )for chunk, vector in zip(chunks, vectors)]
        if points:
            self.client.upsert(
                collection_name=name,
                points=points,
                wait=True,
            )

    def count(self, name):
        return self.client.count(
            collection_name=name,
            exact=True,
        ).count

    def switch_alias(self, name):
        alias = self.settings.qdrant_alias
        exists = any(
            a.alias_name == alias
            for a in self.client.get_aliases().aliases
        )
        actions = []
        if exists:
            actions.append(models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=alias)
            ))
        actions.append(models.CreateAlias(
            collection_name=name,
            alias_name=alias,
        ))
        self.client.update_collection_aliases(
            change_aliases_operations=actions
        )

    def active(self):
        try:
            aliases = self.client.get_aliases().aliases
            name = next(
                (
                    a.collection_name
                    for a in aliases
                    if a.alias_name == self.settings.qdrant_alias
                ),
                None,
            )
            if not name:
                raise ValueError('尚无活动知识库, 请先导入')
            path = self.manifest_dir / f'{name}.json'
            manifest = json.loads(path.read_text(encoding='utf-8'))
            if manifest.get('collection') != name:
                raise ValueError('manifest 的 collection 不匹配')
            info = self.client.get_collection(name)
            vectors = info.config.params.vectors
            if isinstance(vectors, dict) or vectors.size != manifest['dimension']:
                raise ValueError('索引维度与 manifest 不符')
            if vectors.distance != models.Distance.COSINE:
                raise ValueError('索引不是 Cosine 距离')
            return name, manifest
        except Exception as exc:
            raise ServiceUnavailable(f'活动索引不可用({type(exc).__name__}): {exc}') from exc

    def query(self, name, vector, limit):
        try:
            return self.client.query_points(
                collection_name=name,
                query=vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            ).points
        except Exception as exc:
            raise ServiceUnavailable(f'向量查询失败({type(exc).__name__}): {exc}') from exc

    def delete_inactive(self, name):
        if not name.startswith(('rag_', 'tutorial_')):
            raise ValueError('拒绝删除非教程 collection')
        if any(
                a.collection_name == name
                for a in self.client.get_aliases().alises
        ):
            raise ValueError('该 collection 仍有 alias, 拒绝删除')
        self.client.delete_collection(name)




