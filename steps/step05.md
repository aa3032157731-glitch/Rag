# Step 05：部署 Qdrant，编写索引访问层

## 目标与前置条件

完成 step04。本篇操作数据库不需要加载 BGE-M3。Docker Desktop 必须运行 Linux 容器。官方参考：[本地部署](https://qdrant.tech/documentation/quick-start/)、[collection 与 alias](https://qdrant.tech/documentation/manage-data/collections/)。

Qdrant point 包含 ID、vector、payload。只存 vector 无法向聊天模型提供原文，因此三者都要保留。

## 1. 编写 Docker 配置

新建 `infra/.env.example`，复制为 `infra/.env`：

<!-- file: infra/.env.example -->
```dotenv
QDRANT_IMAGE=qdrant/qdrant:v1.14.1
```

新建 `infra/compose.yaml`：

<!-- file: infra/compose.yaml -->
```yaml
name: rag-tutorial
services:
  qdrant:
    image: ${QDRANT_IMAGE:?Please set QDRANT_IMAGE}
    ports:
      - "127.0.0.1:6333:6333"
    volumes:
      - rag_qdrant_data:/qdrant/storage
    restart: unless-stopped
volumes:
  rag_qdrant_data:
    name: rag_qdrant_data
```

使用固定服务端版本配合 step01 的客户端。Windows 持久化用 Docker 命名卷，不直接挂载项目文件夹。只监听本机，不需要把数据库开放到局域网。

终端 A，根目录：

```powershell
docker version
docker compose --env-file infra/.env -f infra/compose.yaml config
docker compose --env-file infra/.env -f infra/compose.yaml up -d
docker compose --env-file infra/.env -f infra/compose.yaml ps
Invoke-RestMethod 'http://127.0.0.1:6333/collections'
```

首次启动需要下载镜像。`config` 是配置检查；`up -d` 后服务在后台运行，终端可继续使用。成功响应包含 collections 列表，首次为空。

## 2. 新建数据库封装

文件：`backend/app/vector_store.py`。本篇定义完整接口，manifest 和正式 alias 将在 step06 创建。

<!-- file: backend/app/vector_store.py -->
```python
import json
from pathlib import Path

from qdrant_client import QdrantClient, models

from backend.app.config import ROOT
from backend.app.models import ServiceUnavailable


class VectorStore:
    def __init__(self, settings, client=None, manifest_dir=None):
        self.settings = settings
        self.client = client or QdrantClient(url=settings.qdrant_url, timeout=15)
        self.manifest_dir = Path(manifest_dir or ROOT / 'data' / 'manifests')

    def close(self):
        self.client.close()

    def create(self, name, dimension):
        self.client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )

    def upsert(self, name, chunks, vectors):
        if len(chunks) != len(vectors):
            raise ValueError('片段数与向量数不同')
        points = [models.PointStruct(
            id=chunk.chunk_id, vector=vector,
            payload={**chunk.payload(), 'index_version': name},
        ) for chunk, vector in zip(chunks, vectors)]
        if points:
            self.client.upsert(collection_name=name, points=points, wait=True)

    def count(self, name):
        return self.client.count(collection_name=name, exact=True).count

    def switch_alias(self, name):
        alias = self.settings.qdrant_alias
        exists = any(a.alias_name == alias for a in self.client.get_aliases().aliases)
        actions = []
        if exists:
            actions.append(models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=alias)
            ))
        actions.append(models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=name, alias_name=alias)
        ))
        self.client.update_collection_aliases(change_aliases_operations=actions)

    def active(self):
        try:
            aliases = self.client.get_aliases().aliases
            name = next((a.collection_name for a in aliases
                         if a.alias_name == self.settings.qdrant_alias), None)
            if not name:
                raise ValueError('尚无活动知识库，请先导入')
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
            raise ServiceUnavailable(f'活动索引不可用：{exc}') from exc

    def query(self, name, vector, limit):
        try:
            return self.client.query_points(
                collection_name=name, query=vector,
                limit=limit, with_payload=True, with_vectors=False,
            ).points
        except Exception as exc:
            raise ServiceUnavailable(f'向量查询失败：{exc}') from exc

    def delete_inactive(self, name):
        # 只允许显式清理本教程的非活动 collection。
        if not name.startswith(('rag_', 'tutorial_')):
            raise ValueError('拒绝删除非教程 collection')
        if any(a.collection_name == name for a in self.client.get_aliases().aliases):
            raise ValueError('该 collection 仍有 alias，拒绝删除')
        self.client.delete_collection(name)
```

### 代码解释

- `client=None` 允许测试时传入模拟对象，默认才连接真实数据库。
- `wait=True` 表示等待写入完成再做计数或切换，不先宣布成功。
- `switch_alias` 把删除旧 alias 与建立新 alias 放在一次更新操作中。旧 collection 不删除，所以可以回滚。
- `active()` 读取实际 collection 和它对应的 manifest。检索会使用解析出的版本名，避免一次请求中途切换后，来源版本与数据不一致。
- 正式索引不存在或元数据损坏都属于“服务未就绪”，不能返回“没有相关资料”。

## 3. 新建数据库冒烟测试

文件：`backend/scripts/check_qdrant.py`。使用独立的 `tutorial_` collection，不碰活动知识库；支持重启后再查询。

<!-- file: backend/scripts/check_qdrant.py -->
```python
import argparse
from uuid import uuid4
from qdrant_client import models

from backend.app.config import load_settings
from backend.app.vector_store import VectorStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', help='重启后查询上次创建的测试 collection')
    args = parser.parse_args()
    store = VectorStore(load_settings())
    name = args.name or 'tutorial_' + uuid4().hex[:12]
    try:
        if not args.name:
            store.create(name, 3)
            store.client.upsert(name, points=[
                models.PointStruct(id=1, vector=[1.0, 0.0, 0.0], payload={'text': '甲'}),
                models.PointStruct(id=2, vector=[0.0, 1.0, 0.0], payload={'text': '乙'}),
            ], wait=True)
        rows = store.query(name, [1.0, 0.0, 0.0], 1)
        assert rows and rows[0].payload['text'] == '甲'
        print('验证通过，collection：', name)
        print('重启后运行：python -m backend.scripts.check_qdrant --name', name)
    finally:
        store.close()


if __name__ == '__main__':
    main()
```

终端 A：

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.check_qdrant
docker compose --env-file infra/.env -f infra/compose.yaml restart
```

等 `/collections` 再次可访问，用脚本打印的真实名称替换下方名称，继续执行：

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.check_qdrant --name tutorial_替换成实际名称
```

这条命令里的中文是需要替换的参数，不是可直接复制的名称。再次“验证通过”证明数据持久化。测试 collection 可以暂时保留，它不属于 `rag_active`，不会被正式检索。

## 4. 排查与验收

| 现象 | 检查与解决 |
| --- | --- |
| docker version 只有 Client 没有 Server | 启动 Docker Desktop 并等引擎就绪 |
| 6333 被占用 | 查看已有服务；不要误关闭其他项目，可统一改 Compose 和 backend/.env 端口 |
| v1.14.1 拉取失败 | 查看网络或镜像仓库错误，不静默换 latest；固定版本便于定位问题 |
| active 报尚无知识库 | 此阶段正常，step06 才创建活动 alias |
| 重启后查不到 | 检查是否用了同名卷，以及是否查了脚本第一次打印的 collection |

- [ ] Docker 后台服务正常。
- [ ] 测试向量能查到正确 payload。
- [ ] 重启后数据仍存在。

下一篇：[step06：导入和更新知识库](step06.md)。
