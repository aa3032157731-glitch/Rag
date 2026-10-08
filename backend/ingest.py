from backend.app.config import load_settings
from backend.app.embedding import EmbeddingService
from backend.app.loader import load_documents
from backend.app.splitter import split_document, fit_token_budget, embedding_text
from backend.app.vector_store import VectorStore


def load_chunks(settings):
    report = load_documents(settings.documents_dir)
    if report.errors:
        raise ValueError('资料有错误 \n' + '\n'.join(report.errors))
    chunks = [
        chunk
        for doc in report.documents
        for chunk in split_document(doc, settings.chunk_size, settings.chunk_overlap)
    ]
    if not chunks:
        raise ValueError(f'{settings.documents_dir}里没有可导入的资料')
    return chunks

def ingest(chunks, model, store, max_tokens):
    chunks = fit_token_budget(chunks, model.count_tokens, max_tokens)
    vectors = model.encode_documents([embedding_text(c) for c in chunks])
    store.recreate(model.dimension)
    store.upsert(chunks, vectors)
    return store.count()

def main():
    settings = load_settings()
    chunks = load_chunks(settings)
    print('字符分段: ', len(chunks))
    model = EmbeddingService(settings)
    store = VectorStore(settings)
    try:
        count = ingest(chunks, model, store, settings.max_tokens)
        print(f'导入完成: {store.name}共{count}条')
    finally:
        store.close()

if __name__ == '__main__':
    main()