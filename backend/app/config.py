import json
import os
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / 'backend' /'.env',
        env_file_encoding='utf-8',
        extra='forbid'
    )
    documents_dir: Path = Path('data/documents')
    model_cache: Path = Path('models/huggingface')
    model_name: str = 'BAAI/bge-m3'
    model_revision: str = '5617a9f61b028005a4858fdac845db406aefb181'
    device: str = 'cpu'
    fp16: bool = False
    batch_size: int = 1
    max_tokens: int = 1024
    chunk_size: int = 600
    chunk_overlap: int = 100
    qdrant_url: str = 'http//127.0.0.1:6333'
    qdrant_alias: str = 'rag_active'
    top_k: int = 5
    max_context_chars: int = 4000
    min_score: float | None = None

    @field_validator('min_score', mode='before')
    @classmethod
    def empty_score(cls, value):
        return None if value == '' else value

    @model_validator(mode='after')
    def validate_values(self):
        if self.device not in ('cpu', 'cuda'):
            raise ValueError('DEVICE 只能是 cpu 或 cuda')
        if self.device == 'cpu' and self.fp16:
            raise ValueError('CPU 基线请设置 FP16=false')
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError('必须 0 <= CHUNK_OVERLAP < CHUNK_SIZE')
        if not 16 <= self.chunk_size <= 10000:
            raise ValueError('CHUNK_SIZE 必须在 16～10000')
        if not 32 <= self.max_tokens <= 8192:
            raise ValueError('MAX_TOKENS 必须在 32～8192')
        if self.batch_size < 1 or not 1 <= self.top_k <= 10:
            raise ValueError('BATCH_SIZE >= 1，TOP_K 在 1～10')
        if not 64 <= self.max_context_chars <= 20000:
            raise ValueError('MAX_CONTEXT_CHARS 必须在 64～20000')
        if self.min_score is not None and not -1 <= self.min_score <= 1:
            raise ValueError('MIN_SCORE 必须留空或在 -1～1')
        if not self.qdrant_url.startswith(('http://', 'https://')):
            raise ValueError('QDRANT_URL 必须包含 http:// 或 https://')
        for name in ('documents_dir', 'model_cache'):
            value = getattr(self, name)
            setattr(self, name, (ROOT / value).resolve())
        return self

    def prepare_cache(self):
        self.model_cache.mkdir(parents=True, exist_ok=True)
        os.environ['HF_HOME'] = str(self.model_cache)


def load_settings():
    return Settings()

if __name__ == '__main__':
    settings = load_settings()
    print(json.dumps(settings.model_dump(mode='json'), ensure_ascii=False, indent=2))