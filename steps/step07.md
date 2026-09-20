# Step 07：实现命令行检索

## 目标与前置条件

完成 step06，Qdrant 正在运行，`rag_active` 指向成功导入的索引。其他模型进程退出。本篇输入问题后返回原文，不调用 DeepSeek。

我们把模型和数据库的细节封装起来，让命令行与后面的 HTTP 接口调用同一个 Retriever。

## 1. 新建 `backend/app/retriever.py`

<!-- file: backend/app/retriever.py -->
```python
from dataclasses import asdict
import re
import time

from backend.app.models import Hit, ServiceUnavailable


def normalized(text):
    return re.sub(r'\s+', '', text)


def near_duplicate(left, right):
    a, b = normalized(left), normalized(right)
    if a == b:
        return True
    if not a or not b:
        return False
    def grams(text):
        return {text[i:i + 3] for i in range(max(1, len(text) - 2))}
    x, y = grams(a), grams(b)
    return len(x & y) / max(1, len(x | y)) >= 0.85


class Retriever:
    def __init__(self, settings, model, store):
        self.settings = settings
        self.model = model
        self.store = store

    def ready(self):
        name, manifest = self.store.active()
        if manifest.get('fingerprint') != self.model.fingerprint():
            raise ServiceUnavailable('模型或分段配置与索引不匹配，请停止服务并重新导入')
        if manifest.get('dimension') != self.model.dimension:
            raise ServiceUnavailable('模型维度与索引不匹配')
        return name

    def retrieve(self, query, top_k=None):
        query = query.strip()
        if not query or len(query) > 2000:
            raise ValueError('问题必须是 1～2000 个字符')
        top_k = self.settings.top_k if top_k is None else top_k
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError('top_k 必须是 1～10 的整数')
        start = time.perf_counter()
        name = self.ready()
        encode_start = time.perf_counter()
        vector = self.model.encode_query(query)
        encode_end = time.perf_counter()
        candidates = self.store.query(name, vector, min(40, top_k * 4))
        search_end = time.perf_counter()
        selected = []
        used = 0
        for row in candidates:
            if self.settings.min_score is not None and row.score < self.settings.min_score:
                continue
            payload = row.payload or {}
            if not payload.get('text') or not payload.get('source'):
                raise ServiceUnavailable('索引片段缺少正文或来源，请重建')
            hit = Hit(
                chunk_id=str(row.id), source=payload['source'],
                title=payload.get('title', ''), section=payload.get('section', ''),
                record_key=payload.get('record_key', ''), text=payload['text'], score=float(row.score),
            )
            if any(h.source == hit.source and h.section == hit.section
                   and near_duplicate(h.text, hit.text) for h in selected):
                continue
            # 保留整段，不截断句子。字符预算小于片段时跳过该段。
            if used + len(hit.text) > self.settings.max_context_chars:
                continue
            selected.append(hit)
            used += len(hit.text)
            if len(selected) == top_k:
                break
        return {
            'status': 'ok' if selected else 'no_match',
            'query': query,
            'index_version': name,
            'results': [asdict(h) for h in selected],
            'timings_ms': {
                'embedding': round((encode_end - encode_start) * 1000, 2),
                'search': round((search_end - encode_end) * 1000, 2),
                'total': round((time.perf_counter() - start) * 1000, 2),
            },
        }
```

### 关键解释

- `ready()` 在每次查询前确认模型与索引一致，返回具体版本名。
- `top_k * 4` 多取候选后再去重和选择，避免最终上下文全是相邻重复片段。
- 字符预算只约束资料正文，Unity 后面还有整体提示词限制。
- `no_match` 代表没有片段通过筛选；数据库断开则抛异常，两者不能混淆。
- 不设阈值时，完全无关的问题也可能拿到最近的向量。必须等 step12 标定和检查，不能把 `ok` 理解成“必定有答案”。

## 2. 新建 `backend/cli.py`

<!-- file: backend/cli.py -->
```python
import argparse
import json

from backend.app.config import load_settings
from backend.app.embedding import EmbeddingService
from backend.app.retriever import Retriever
from backend.app.vector_store import VectorStore


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--query')
    mode.add_argument('--interactive', action='store_true')
    parser.add_argument('--top-k', type=int, default=None)
    args = parser.parse_args()
    settings = load_settings()
    print('正在加载向量模型，请稍候……')
    model = EmbeddingService(settings)
    store = VectorStore(settings)
    retriever = Retriever(settings, model, store)
    try:
        print('活动索引：', retriever.ready())
        while True:
            try:
                query = input('问题（输入 exit 结束）：') if args.interactive else args.query
            except (EOFError, KeyboardInterrupt):
                break
            if query.strip().lower() == 'exit':
                break
            try:
                result = retriever.retrieve(query, args.top_k)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except Exception as exc:
                print(f'检索失败：{exc}')
                if not args.interactive:
                    raise SystemExit(1) from exc
            if not args.interactive:
                break
    finally:
        store.close()


if __name__ == '__main__':
    main()
```

循环模式在 while 外创建模型，所有问题共用一份。用 `finally` 保证退出时关闭数据库客户端，即使发生异常也会执行。

## 3. 新建检索编排测试

文件：`backend/tests/test_retriever.py`。模拟模型用固定向量；测试的是预算、去重、阈值和错误传播，不是 BGE-M3 语义能力。

<!-- file: backend/tests/test_retriever.py -->
```python
from types import SimpleNamespace
import unittest

from backend.app.models import ServiceUnavailable
from backend.app.retriever import Retriever


class Model:
    dimension = 3
    def fingerprint(self):
        return 'fp'
    def encode_query(self, query):
        return [1.0, 0.0, 0.0]


class Store:
    def __init__(self, rows, fingerprint='fp'):
        self.rows = rows
        self.fp = fingerprint
    def active(self):
        return 'rag_test', {'fingerprint': self.fp, 'dimension': 3}
    def query(self, name, vector, limit):
        return self.rows[:limit]


def row(number, text, score=0.8):
    return SimpleNamespace(id=number, score=score, payload={
        'source': 'x.txt', 'text': text, 'title': '标题', 'section': '', 'record_key': 'text',
    })


class RetrieverTests(unittest.TestCase):
    def settings(self, **changes):
        values = {'top_k': 5, 'min_score': None, 'max_context_chars': 10}
        values.update(changes)
        return SimpleNamespace(**values)

    def test_dedup_budget_and_order(self):
        r = Retriever(self.settings(), Model(), Store([
            row(1, '甲乙丙丁'), row(2, '甲乙丙丁'),
            row(3, '戊己庚辛'), row(4, '壬癸子丑'),
        ]))
        data = r.retrieve('问题')
        self.assertEqual([h['chunk_id'] for h in data['results']], ['1', '3'])
        self.assertEqual(data['index_version'], 'rag_test')

    def test_no_match_and_bad_input(self):
        r = Retriever(self.settings(min_score=0.9), Model(), Store([row(1, '正文')]))
        self.assertEqual(r.retrieve('问题')['status'], 'no_match')
        for query, top_k in [('', 5), ('问题', 0), ('问题', True)]:
            with self.assertRaises(ValueError):
                r.retrieve(query, top_k)

    def test_mismatch_is_error_not_no_match(self):
        r = Retriever(self.settings(), Model(), Store([], fingerprint='old'))
        with self.assertRaises(ServiceUnavailable):
            r.retrieve('问题')


if __name__ == '__main__':
    unittest.main()
```

## 4. 运行并观察结果

终端 A，根目录：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_retriever -v
.\.venv\Scripts\python.exe -m backend.cli --interactive
```

按顺序输入：

1. `图书馆几点关门？`：原文中应有 22:00。
2. `晚上九点还能去图书馆吗？`：同义表达应仍能召回开放时间。
3. `校园卡补办需要什么材料？`：应包含身份证和学生证。
4. `明天天气怎么样？`：资料没有答案；未设阈值时仍可能返回片段，记录这种局限。

观察 `timings_ms`，区分模型编码与数据库查询。输出是原文，不要求模型替你推理“九点小于十点”。

单次查询方式：

```powershell
.\.venv\Scripts\python.exe -m backend.cli --query '第一食堂在哪里？' --top-k 3
```

## 5. 排查与验收

| 现象 | 检查与解决 |
| --- | --- |
| 模型/分段不匹配 | 最近改过 .env？停止查询程序，用相同配置重新导入 |
| 空结果 | 检查 MIN_SCORE 是否过高、字符预算是否小于整段长度 |
| 无关问题也返回资料 | 这是近邻检索特性，留到评估标定；不要随便写一个“万能阈值” |
| 查询到旧时间 | 检查活动 alias，修改源文档后必须导入 |

- [ ] 3 个离线测试通过。
- [ ] 至少 3 个已知答案问题能看到正确原文。
- [ ] 能区分检索结果、生成答案和检索失败。
- [ ] 输入 `exit` 后退出，避免下一篇再开一份模型。

下一篇：[step08：通过 FastAPI 暴露接口](step08.md)。
