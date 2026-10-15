"""Past incident write-ups the agent can search, and what a search returns."""

from pydantic import BaseModel


class Postmortem(BaseModel):
    id: str
    title: str
    date: str
    services: list[str]
    body: str


class Hit(BaseModel):
    doc_id: str
    title: str
    score: float
    snippet: str
