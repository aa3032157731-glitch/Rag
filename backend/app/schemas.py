from pydantic import Field, BaseModel

from backend.app.models import Hit


class RetrieveRequest(BaseModel):
    question : str = Field(min_length=1, max_length=500)
    top_k : int | None = Field(default=None, ge=1, le=10)

class RetrieveResponse(BaseModel):
    question: str
    hits: list[Hit]