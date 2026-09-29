import json
import statistics
import time
from datetime import datetime, timezone

from backend.app.config import ROOT, load_settings
from backend.app.embedding import EmbeddingService


def main():
    settings = load_settings()
    start = time.perf_counter()
    model = EmbeddingService(settings)
    loading = time.perf_counter() - start
    texts = [
        '图书馆每天八点开门，晚上十点关门。',
        '图书馆夜间什么时候闭馆？',
        '第一食堂二楼有素食窗口。',
    ]
    vectors = model.encode_documents(texts)
    cosine = lambda a, b: sum(x * y for x, y in zip(a, b))
    print('维度：', len(vectors[0]))
    print('相关文本分数：', cosine(vectors[0], vectors[1]))
    print('无关文本分数：', cosine(vectors[0], vectors[2]))
    times = []
    for index in range(30):
        start = time.perf_counter()
        model.encode_query(texts[index % len(texts)])
        times.append((time.perf_counter() - start) * 1000)
    report = {
        'at': datetime.now(timezone.utc).isoformat(),
        'device': settings.device,
        'fingerprint': model.fingerprint(),
        'download_and_load_seconds': round(loading, 3),
        'query_count': len(times),
        'p50_ms': round(statistics.median(times), 2),
        'p95_ms': round(sorted(times)[28], 2),
        'samples_ms': times,
    }
    destination = ROOT / 'docs' / 'embedding-benchmark.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()