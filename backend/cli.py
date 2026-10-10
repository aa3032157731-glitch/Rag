from backend.app.retriever import Retriever
from backend.app.config import load_settings, Settings
from backend.app.embedding import EmbeddingService
from backend.app.vector_store import VectorStore


def main():
    settings = load_settings()
    model = EmbeddingService(settings)
    store = VectorStore(settings)
    retriever = Retriever(model, store, settings.top_k, settings.max_context_chars, settings.min_score)
    try:
        while True:
            question = input('\n问题(直接回车退出): ').strip()
            if not question:
                break
            hits = retriever.search(question)
            if not hits:
                print('没有找到相关资料')
            for i, hit in enumerate(hits, 1):
                print(f'[{i}] {hit.score:.3f} {hit.score} {hit.section}')
                print('     ' + hit.text.replace('\n', ' '))
    finally:
        store.close()

if __name__ == '__main__':
    main()