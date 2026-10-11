from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from backend.app.config import load_settings
from backend.app.embedding import EmbeddingService
from backend.app.retriever import Retriever
from backend.app.schemas import RetrieveResponse, RetrieveRequest
from backend.app.vector_store import VectorStore


def create_app(retriever=None):
    @asynccontextmanager
    async def lifespan(app):
        if retriever is not None:
            app.state.retriever = retriever
            yield
            return
        settings = load_settings()
        store = VectorStore(settings)
        app.state.retriever = Retriever(
            EmbeddingService(settings), store,
            settings.top_k, settings.max_context_chars, settings.min_score,
        )
        yield
        store.close()

    app = FastAPI(title='RAG检索服务', lifespan=lifespan)

    @app.get('/health')
    def health():
        return {'status' : 'ok'}

    @app.post('/retrieve', response_model=RetrieveResponse)
    def retrieve(request: RetrieveRequest):
        try:
            hits = app.state.retriever.search(request.question, request.top_k)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except (ResponseHandlingException, UnexpectedResponse) as exc:
            raise HTTPException(status_code=503, detail=f'向量数据库不可用')
        return RetrieveResponse(question=request.question, hits=hits)
    return app

app = create_app()