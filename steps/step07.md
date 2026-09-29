# Step 07：实现命令行检索

> **本节改动说明**：把候选召回数量里的魔法数字 `min(40, top_k * 4)` 提成了两个命名常量 `CANDIDATE_CAP` 和 `CANDIDATE_MULTIPLIER`，行为完全不变，但以后想调这两个数字时不用再去代码里找那一行藏在哪。其余逻辑与原教程一致。

## 本节目标

完成后你能：在命令行输入问题，得到经过去重、按字符预算裁剪过的检索原文，并且清楚区分"没有匹配"和"服务出错"这两种情况。

**前置条件**：完成 step06，Qdrant 正在运行，`rag_active` 指向一次成功的导入。退出其他模型进程。本篇**不**调用 DeepSeek，只做检索。

这里把检索的核心逻辑封装进 `Retriever`，让命令行工具和 step08 的 HTTP 接口调用同一份代码——这是本篇最重要的设计决定：**检索算法只写一次**。

## 一、新建 `backend/app/retriever.py`

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
    # 先多取一些候选，去重和按字符预算裁剪后再返回，避免最终结果全是相邻重复片段。
    CANDIDATE_MULTIPLIER = 4
    CANDIDATE_CAP = 40

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
        candidate_limit = min(self.CANDIDATE_CAP, top_k * self.CANDIDATE_MULTIPLIER)
        candidates = self.store.query(name, vector, candidate_limit)
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
            # 保留整段，不截断句子；字符预算装不下这一整段时就跳过它。
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

## 二、关键代码解读

- **`ready()` 每次查询前都会检查**：模型指纹和 manifest 记录的是否一致。如果你改了 `.env` 里的 `MODEL_REVISION`、`CHUNK_SIZE` 等参数却忘了重新导入，这里会直接报错，而不是让你拿新模型的向量去查旧索引、得到语义上完全不对的结果。
- **为什么要多取候选（`CANDIDATE_MULTIPLIER`）**：如果只精确取 `top_k` 条，去重和字符预算裁剪掉几条之后，可能凑不够数量。先多要一些再筛选，是检索系统里常见的做法。
- **`no_match` 和异常是两回事**：`no_match` 表示"数据库正常响应，但没有片段通过筛选"；数据库连不上、alias 不存在等情况会直接抛出 `ServiceUnavailable`。两者绝对不能混在一起返回，否则前端没法区分"知识库确实没有答案"和"系统坏了"。
- **没设 `min_score` 时的局限**：向量检索总能找到"相对最近"的结果，哪怕问题和资料完全不相关。这个局限要留到 step12 用评估集来标定阈值，本篇先如实呈现这个现象，不做虚假的兜底。

## 三、新建 `backend/cli.py`

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

循环模式下模型只在 `while` 外创建一次，所有问题共用同一份，避免每问一次就重新加载一次模型。`finally` 保证不管中途是否抛异常，退出前都会关闭数据库连接。

## 四、新建检索编排测试

文件：`backend/tests/test_retriever.py`。这里用的是固定向量的模拟模型，测试目标是预算控制、去重和错误传播这几个"编排逻辑"，不是 BGE-M3 的语义能力（那部分在 step04 已经单独测过）。

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

## 五、运行并观察结果

终端 A，项目根目录：

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_retriever -v
.\.venv\Scripts\python.exe -m backend.cli --interactive
```

按顺序试试这几个问题，边看输出边理解系统行为：

1. `图书馆几点关门？` —— 原文里应该有 22:00。
2. `晚上九点还能去图书馆吗？` —— 同义表达，应该仍能召回开放时间那一段。
3. `校园卡补办需要什么材料？` —— 应该包含身份证和学生证。
4. `明天天气怎么样？` —— 资料里没有答案；由于还没设阈值，可能仍会返回某个片段，这正是上面提到的局限，先记录下来。

留意每次返回的 `timings_ms`，区分"模型编码耗时"和"数据库查询耗时"——这是后面排查性能问题的关键线索。输出的是原文，模型不会替你做"九点小于十点"这类推理，那是 step11 聊天模型的工作。

单次查询：

```powershell
.\.venv\Scripts\python.exe -m backend.cli --query '第一食堂在哪里？' --top-k 3
```

## 六、排查与验收

| 现象 | 检查与解决 |
| --- | --- |
| 提示模型/分段不匹配 | 最近是否改过 `.env`？停掉查询程序，用当前配置重新导入一次 |
| 结果为空 | 检查 `MIN_SCORE` 是不是设得太高，或者字符预算是不是小于单个片段的长度 |
| 无关问题也返回了资料 | 这是近邻检索的固有特性，留到 step12 标定；不要现在就随手写一个"看起来合适"的阈值 |
| 查到的是旧数据 | 检查当前活动 alias，改完源文档必须重新导入才会生效 |

- [ ] 3 个离线测试全部通过。
- [ ] 至少 3 个已知答案的问题都能看到正确原文。
- [ ] 能清楚分辨"检索结果""生成答案""检索失败"这三种不同的东西。
- [ ] 输入 `exit` 正常退出，避免下一篇又重复加载一份模型。

下一篇：[step08：通过 FastAPI 暴露接口](step08.md)。
