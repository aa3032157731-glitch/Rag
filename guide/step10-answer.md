# Step 10 参考答案

`evaluate.py` 已经跑过：测试全部通过，也用真实的 BGE-M3 在合成资料上跑过评估。真实资料和数字人端到端对比这两部分，只能由你来做；我能给的是读结果的方法。

## backend/scripts/evaluate.py

```python
import json
from statistics import mean

from backend.app.config import ROOT, load_settings
from backend.app.embedding import EmbeddingService
from backend.app.retriever import Retriever
from backend.app.vector_store import VectorStore


def load_cases(path):
    with open(path, encoding='utf-8') as file:
        return [json.loads(line) for line in file if line.strip()]


def judge(case, hits, min_score):
    top1 = hits[0].score if hits else 0.0
    answer = case['answer']
    if answer is None:
        return {'question': case['question'], 'answerable': False, 'rank': None,
                'top1': top1, 'ok': top1 < min_score}
    rank = next((i for i, hit in enumerate(hits, 1) if answer in hit.text), None)
    return {'question': case['question'], 'answerable': True, 'rank': rank,
            'top1': top1, 'ok': rank is not None and hits[rank - 1].score >= min_score}


def summarize(rows):
    def rate(items, test):
        return round(mean(1 if test(r) else 0 for r in items), 3) if items else None

    answerable = [r for r in rows if r['answerable']]
    unanswerable = [r for r in rows if not r['answerable']]
    return {
        'hit@1': rate(answerable, lambda r: r['rank'] == 1),
        'hit@3': rate(answerable, lambda r: r['rank'] is not None and r['rank'] <= 3),
        'kept_by_threshold': rate(answerable, lambda r: r['ok']),
        'rejected_unanswerable': rate(unanswerable, lambda r: r['ok']),
    }


def main():
    settings = load_settings()
    min_score = settings.min_score or 0.0
    cases = load_cases(ROOT / 'data' / 'evaluations' / 'questions.jsonl')
    store = VectorStore(settings)
    retriever = Retriever(EmbeddingService(settings), store, top_k=5, max_context_chars=10 ** 9)
    try:
        rows = [judge(case, retriever.search(case['question']), min_score) for case in cases]
    finally:
        store.close()
    for row in sorted(rows, key=lambda r: (r['answerable'], r['top1'])):
        mark = 'ok' if row['ok'] else 'XX'
        kind = f"rank={row['rank']}" if row['answerable'] else '无答案'
        print(f"{mark}  {row['top1']:.3f}  {kind:<8} {row['question']}")
    print(f'MIN_SCORE = {min_score}')
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
```

`max_context_chars=10 ** 9` 的意思是"评估时不限制预算"。评估要看的是检索本身准不准，不能让预算限制干扰结果。

---

## 合成资料的实测结果（MIN_SCORE=0.45）

```text
ok  0.342  无答案    讲个笑话
ok  0.412  无答案    今天天气怎么样
XX  0.526  无答案    宿舍几点熄灯
XX  0.560  无答案    校医院电话是多少
ok  0.537  rank=1   有素食吗
ok  0.573  rank=1   自习区在哪
ok  0.578  rank=1   网络坏了找谁
ok  0.602  rank=1   图书馆在哪
ok  0.615  rank=1   早饭几点开始
ok  0.622  rank=1   第一食堂在哪
ok  0.653  rank=1   图书馆周末开吗
ok  0.655  rank=1   图书馆几点关门
ok  0.666  rank=1   报修网络要提供什么
ok  0.679  rank=1   校园卡丢了怎么办
ok  0.704  rank=1   补卡要带什么证件
ok  0.787  rank=1   补办校园卡要多少钱
{"hit@1": 1, "hit@3": 1, "kept_by_threshold": 1, "rejected_unanswerable": 0.5}
```

我也试了 MIN_SCORE=0.5，`rejected_unanswerable` 还是 0.5。要想把"宿舍熄灯"和"校医院电话"过滤掉，阈值必须高于 0.56，可那样一来，"有素食吗"（0.537）也会被一起过滤掉。

**Hit@1 = 100% 能说明检索系统很好吗？** 不能。只有 5 段资料，而且每段的话题都不一样，选错的机会本来就很少。这个 100% 说明的是**题目太简单**，不是系统好。所以第 2 节一定要换成真实资料。

---

## 哪些参数需要重新 ingest

| 参数 | 需要重新 ingest？ | 原因 |
|---|---|---|
| `CHUNK_SIZE`、`CHUNK_OVERLAP`、`MAX_TOKENS` | **需要** | 它们决定的是库里存的片段长什么样 |
| 资料文件本身 | **需要** | 原因同上 |
| `TOP_K`、`MIN_SCORE`、`MAX_CONTEXT_CHARS` | 不需要 | 它们只影响查询时取多少、过滤掉哪些 |
| `BATCH_SIZE` | 不需要 | 只影响编码速度，不影响结果 |

改了查询类的参数之后，要重启 uvicorn 才会生效，因为配置是在启动时读取的。`evaluate.py` 每次运行都会重新读配置，所以评估时不用重启任何东西。

---

## 分析真实资料时的常见改进方向

每做一项改进，都要用同一套题目重新跑评估，确认它真的有效。

| 问题 | 可以尝试 |
|---|---|
| 切坏了 | 调整 `CHUNK_SIZE`；给 `boundary_position` 加上逗号作为次一级的切分点（step6 实验 2 提到过） |
| 用词对不上 | 用 BGE-M3 自带的 **sparse 向量**做关键词匹配，和 dense 向量一起检索（混合检索）。你的模型本来就能输出它，现在调用时用的是 `return_sparse=False` |
| 片段太杂 | 调小 `CHUNK_SIZE`；或者整理资料，一个文件只讲一件事 |
| 排序不准 | 加一个 reranker，比如 bge-reranker，对 top-10 重新打分 |
| 没答案却通过了阈值 | 阈值解决不了这个问题，只能靠提示词（step9 实验 3），或者加 reranker，它给出的分数区分度更高 |
| 追问 | 先让大模型把追问改写成完整的问题，再拿去检索（step9 答案里提到过） |

---

## 读端到端对比结果时要注意

- **B 模式里，有一题答错了**：先看那一轮的 `[RAG]` 日志。如果资料没检索到，那是检索的问题，回到第 2 节去分析；如果检索到了，模型却还是答错，那是提示词或模型本身的问题。
- **A 模式出现了"编"**：说明即使资料全给了模型，它也会编造。在 B 模式下，这类问题通常会少一些，因为模型看到的资料更少、更聚焦。但这不是一定的，以你实测的结果为准。
- **关于成本**：DeepSeek 的 API 有"上下文硬盘缓存"：请求开头和之前某次请求相同的部分，会按更低的价格计费。所以全文注入的实际花费，可能比单纯按字数估算的要低。具体规则以 DeepSeek 的官方文档为准；我没有在这个项目里实际测过缓存命中率。
- 资料只有几千字时，A 和 B 打成平手是很正常的结果。你的报告里最有价值的部分，不是"谁赢了"，而是"在多大的资料量、什么样的问题上，哪种方式更好，原因是什么"。
