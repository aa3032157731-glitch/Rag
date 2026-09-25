from dataclasses import replace
import re

from backend.app.models import Chunk, Document, digest, stable_id

def text_windows(text: str, size: int, overlap: int) -> list[str]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError('分段参数不合法')
    output = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]

            position = boundary_position(
                text=window,
                target=len(window),
                minimum=max(size // 2, overlap + 1),
                maximum=len(window),
                fallback=len(window),
            )

            end = start + position
        piece = text[start:end].strip()
        if piece:
            output.append(piece)
        if end == len(text):
            break
        start = end - overlap
    return output

def boundary_position(
        text: str,
        target: int,
        minimum: int,
        maximum: int,
        fallback: int,
) -> int:
    candidates = [
        match.end()
        for match in re.finditer(r'[\n。！？；.!?;]+', text)
        if minimum <= match.end() <= maximum
    ]
    if candidates:
        return min(candidates, key=lambda position: - target)
    return fallback

def split_document(doc: Document, size=600, overlap=100) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=stable_id(f'{doc.document_id}/{doc.content_hash}/{i}/{digest(text)}'),
            document_id=doc.document_id,
            source=doc.source,
            title=doc.title,
            section=doc.section,
            record_key=doc.record_key,
            chunk_index=i,
            text=text,
            content_hash=doc.content_hash,
        )
        for i, text in enumerate(text_windows(doc.text, size, overlap))
    ]

def embedding_text(chunk: Chunk) -> str:
    return f'{chunk.title[:80]}\n{chunk.section[80]}\n{chunk.text}'

def fit_token_budget(chunks, count_tokens, limit):
    result = []
    indexes = {}
    for original in chunks:
        pending = [original.text]
        accepted = []
        while pending:
            text = pending.pop()
            trial = replace(original, text=text)
            if count_tokens(embedding_text(trial)) <= limit:
                accepted.append(text)
                continue
            if len(text) <= 1:
                raise ValueError(f'标题或单字符也超过 token 预算: {original.source}')
            middle = len(text) // 2
            split_at = boundary_position(
                text=text,
                target=middle,
                minimum=1,
                maximum=len(text) - 1,
                fallback=middle,
            )
            pending.extend([text[split_at:], text[:split_at]])
        for text in accepted:
            index = indexes.get(original.document_id, 0)
            indexes[original.document_id] = index + 1
            result.append(replace(
                original,
                text=text,
                chunk_index=index,
                chunk_id=stable_id(f'{original.chunk_id}/token/{index}/{digest(text)}'),
            ))
    return result

if __name__ == '__main__':
    from backend.app.config import load_settings
    from backend.app.loader import load_documents

    settings = load_settings()
    report = load_documents(settings.documents_dir)
    if report.errors:
        raise SystemExit('\n'.join(report.errors))
    chunks = [c for doc in report.documents for c in split_document(
        doc, settings.chunk_size, settings.chunk_overlap)]
    for chunk in chunks:
        print(chunk.chunk_id, chunk.source, chunk.section)
        print(chunk.text)

    print('字符分段数量: ', len(chunks))