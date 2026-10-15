"""What agents hand back. Each answer model is also the schema of the tool that
submits it (see `application/terminal.py`), so the contract is written once."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from rootcause.domain.scenario import Category


class Diagnosis(BaseModel):
    """Report the root cause. Ends the investigation."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    service: str = Field(description="The service where the cause lives.")
    summary: str = Field(description="Two or three sentences.")
    evidence: list[str] = Field(
        min_length=1, description="Ids returned by the tools that support this diagnosis."
    )
    recommended_action: str
    confidence: float = Field(ge=0, le=1, description="0 to 1.")


class Findings(BaseModel):
    """Report what you found. Ends your part of the investigation."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="What the evidence shows, in two to four sentences.")
    evidence: list[str] = Field(description="Ids returned by the tools that support the summary.")
    suspected_service: str | None = Field(description="Service most likely at fault, if any.")
    confidence: float = Field(ge=0, le=1, description="0 to 1.")


class Outcome(StrEnum):
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    STEP_LIMIT = "step_limit"
    BUDGET_EXCEEDED = "budget_exceeded"
    REFUSED = "refused"
    ERROR = "error"
