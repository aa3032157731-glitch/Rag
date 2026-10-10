from backend.app.models import Hit


class Retriever:
    def __init__(self, model, store, top_k=5, max_context_chars=4000, min_score=None):
        self.model = model
        self.store = store
        self.top_k = top_k
        self.max_context_chars = max_context_chars
        self.min_score = min_score
    def search(self, question, top_k=None):
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空")
        rows = self.store.query(self.model.encode_query(question), top_k or self.top_k)
        hits = []
        used = 0
        for row in rows:
            if self.min_score is not None and row.score < self.min_score:
                break
            text = row.payload['text']
            if used + len(text) > self.max_context_chars:
                break
            used += len(text)
            hits.append(Hit(
                chunk_id=row.payload['chunk_id'],
                source=row.payload['source'],
                title=row.payload['title'],
                section=row.payload['section'],
                record_key=row.payload['record_key'],
                text=text,
                score=row.score,
            ))
        return hits