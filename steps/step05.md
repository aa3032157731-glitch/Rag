# Step 05：部署 Qdrant，编写索引访问层

> **本节改动说明**（相对原教程）：
> 1. 统一了各篇的标题结构，方便你跳读；
> 2. `VectorStore.query()` / `active()` 抛出的错误信息里加上了具体异常类型（如 `ConnectionError`），排错时不用先猜是网络问题还是数据问题；
> 3. 补充了"为什么这么设计"的简短说明，减少你需要自己脑补的部分。
> 代码的整体结构和原教程一致，没有改变任何行为，改动都可以直接对照 diff 理解。

## 本节目标

完成后你能：本机通过 Docker 跑起 Qdrant，并写好一层 Python 封装（`VectorStore`），支持创建 collection、写入向量、按 alias 查询、以及安全地切换"当前生效版本"。

**前置条件**：完成 [step04](step04.md)。本篇不需要加载 BGE-M3。Docker Desktop 必须切换到 Linux 容器模式。

## 核心概念

在动手之前，先弄清楚三个词，因为它们是本篇设计的核心：

| 概念 | 含义 | 在本项目里的用法 |
| --- | --- | --- |
| **collection** | Qdrant 里一组向量 + 元数据的容器，相当于一张表 | 每次全量重建都会生成一个**新** collection，名字带时间戳，例如 `rag_20260917_103000_ab12cd34` |
| **payload** | 和向量一起存的原文、来源、标题等字段 | 只存向量、不存 payload 的话，检索到结果后没有原文可以喂给聊天模型 |
| **alias** | 指向某个 collection 的稳定别名 | 代码永远只查 `rag_active` 这个 alias，不直接写死某个 collection 名字，这样才能做到"新版本导入失败，旧版本继续可用" |

一句话记住本篇的设计目标：**旧的不删，新的建好才切换**。

官方参考：[Qdrant 本地部署](https://qdrant.tech/documentation/quick-start/)、[collection 与 alias](https://qdrant.tech/documentation/manage-data/collections/)。

## 一、编写 Docker 配置

新建 `infra/.env.example`，复制一份为 `infra/.env`：

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

几个细节值得注意：

- `${QDRANT_IMAGE:?...}` 是故意的：不设置版本号就直接报错退出，而不是悄悄拉取 `latest`。固定版本号是为了让你遇到问题时能复现，而不是"上周还好好的，这周镜像自动更新后就不行了"。
- 端口只绑定 `127.0.0.1`，不开放到局域网——本机学习项目没必要暴露数据库端口。
- Windows 上用 Docker **命名卷**（`rag_qdrant_data`）而不是直接挂载项目文件夹，这样即使你以后换了 Compose 文件所在目录，数据也不会因为"挂载点变了"而看似丢失。

终端 A，项目根目录：

```powershell
docker version
docker compose --env-file infra/.env -f infra/compose.yaml config
docker compose --env-file infra/.env -f infra/compose.yaml up -d
docker compose --env-file infra/.env -f infra/compose.yaml ps
Invoke-RestMethod 'http://127.0.0.1:6333/collections'
```

`config` 只是打印最终配置，用来检查有没有拼写错误；`up -d` 才会真正在后台启动服务，之后这个终端可以继续做别的事。首次启动要下载镜像，成功后 `/collections` 应该返回一个空列表。

## 二、新建数据库封装层

文件：`backend/app/vector_store.py`。这是本篇的核心文件，正式的 alias 切换、manifest 记录会在 step06 用到，这里先把接口定义完整。

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
            # 附带异常类型名，方便一眼区分"连不上数据库"和"manifest 内容不对"。
            raise ServiceUnavailable(f'活动索引不可用（{type(exc).__name__}）：{exc}') from exc

    def query(self, name, vector, limit):
        try:
            return self.client.query_points(
                collection_name=name, query=vector,
                limit=limit, with_payload=True, with_vectors=False,
            ).points
        except Exception as exc:
            raise ServiceUnavailable(f'向量查询失败（{type(exc).__name__}）：{exc}') from exc

    def delete_inactive(self, name):
        # 只允许显式清理本教程的非活动 collection，防止误删别的项目数据。
        if not name.startswith(('rag_', 'tutorial_')):
            raise ValueError('拒绝删除非教程 collection')
        if any(a.collection_name == name for a in self.client.get_aliases().aliases):
            raise ValueError('该 collection 仍有 alias，拒绝删除')
        self.client.delete_collection(name)
```

## 三、关键代码解读

- **依赖注入**：`client=None` 允许测试时传入模拟对象，正常使用时才连接真实数据库。后面 step06/07 的单元测试都是靠这一点跳过真实网络请求的。
- **`wait=True`**：写入调用会等 Qdrant 真正落盘完成后才返回，避免"上一步刚说写完，下一步立刻查却查不到"的竞态问题。
- **`switch_alias` 是原子操作**：删除旧 alias 和创建新 alias 放在同一次 `update_collection_aliases` 调用里，Qdrant 保证这一步不会出现"中途状态"。旧 collection 本身不删，所以理论上可以手动切回去做回滚。
- **`active()` 做了三层校验**：alias 存在 → manifest 文件存在且内容自洽 → 数据库里的实际维度和距离度量与 manifest 记录的一致。任何一层失败都统一包装成 `ServiceUnavailable`，因为对上层调用者（检索、API）来说，"活动索引不可用"就该被当成服务未就绪，而不是"没有相关资料"。
- **为什么 `query()` 也要包一层异常**：把 Qdrant 客户端库可能抛出的各种底层异常（连接超时、gRPC 错误等）统一转换成本项目自己的 `ServiceUnavailable`，上层代码就只需要认识这一种异常类型，不用去关心 Qdrant 客户端内部的异常体系。

## 四、新建数据库冒烟测试

文件：`backend/scripts/check_qdrant.py`。它使用一个独立的 `tutorial_` 前缀 collection，不会碰到你正式的知识库；支持重启 Qdrant 后再次查询，用来验证持久化。

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

> 注意这里用的是 `store.client.upsert(...)`（直接调用底层客户端），不是 `store.upsert(...)`（后者需要传入 `Chunk` 对象）。这是有意的：冒烟测试只想验证数据库本身能读写，不想牵扯 `Chunk`/`models.py` 那一层，两者不是笔误。

终端 A：

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.check_qdrant
docker compose --env-file infra/.env -f infra/compose.yaml restart
```

等 `/collections` 重新可访问后，把脚本打印出来的真实 collection 名字替换进下面命令再执行一次（命令里的中文是要你替换的占位符，不是可以直接复制的字面量）：

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.check_qdrant --name tutorial_替换成实际名称
```

再次看到"验证通过"就说明数据经过重启依然还在。这个测试 collection 不属于 `rag_active`，留着不会影响后续检索。

## 五、排查与验收

| 现象 | 检查与解决 |
| --- | --- |
| `docker version` 只有 Client、没有 Server | 启动 Docker Desktop 并等引擎就绪 |
| 6333 端口被占用 | 排查是否有其他项目在用；不要误关别的服务，也可以统一改 Compose 和 `backend/.env` 里的端口 |
| `v1.14.1` 拉取失败 | 检查网络或镜像源，不要为图方便静默换成 `latest`；固定版本才好定位问题 |
| `active()` 报"尚无活动知识库" | 这个阶段是正常的，`rag_active` 要到 step06 导入后才会出现 |
| 重启后查不到数据 | 检查是否用了同一个命名卷，以及是否查询了脚本第一次打印出来的那个 collection 名 |

- [ ] Docker 后台服务正常运行。
- [ ] 冒烟测试的向量能查到正确的 payload。
- [ ] 重启容器后数据依然存在。

下一篇：[step06：导入和更新知识库](step06.md)。
