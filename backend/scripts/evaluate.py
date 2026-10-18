import argparse
import json
from pathlib import Path
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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'questions',
        nargs='?',
        type=Path,
        default=ROOT / 'data' / 'evaluations' / 'questions.jsonl',
        help='题库 jsonl 文件, 默认 data/evaluations/questions.jsonl',
    )
    args = parser.parse_args()
    settings = load_settings()
    min_score = settings.min_score or 0.0
    cases = load_cases(args.questions)
    store = VectorStore(settings)
    # 评估看的是检索本身: 取回 top5 原始结果, 不做分数过滤和上下文预算, 阈值只在 judge 里用
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
