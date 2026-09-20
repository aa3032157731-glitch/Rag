from dataclasses import dataclass, asdict
from hashlib import sha256
from uuid import uuid5, NAMESPACE_URL


def digest(text: str) -> str:
    return sha256(text.encode('utf-8')).hexdigest()

def stable_id(text: str) -> str:
    return str(uuid5(NAMESPACE_URL, 'rag-tutorial' + text))

@dataclass(frozen=True)
class Document:
    document_id: str
    source: str
    title: str
    section: str
    record_key: str
    text: str
    content_hash: str

@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    source: str
    title: str
    section: str
    record_key: str
    chunk_index: int
    text: str
    content_hash: str

    def payload(self):
        return asdict(self)

@dataclass(frozen=True)
class Hit:
    chunk_id: str
    source: str
    title: str
    section: str
    record_key: str
    text: str
    score: float

class ServiceUnavailable(RuntimeError):
    pass
