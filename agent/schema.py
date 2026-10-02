from datetime import date as Date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def _validate_iso_date(value: str) -> str:
    """Keep agent handoff dates JSON-safe while rejecting invalid dates."""
    try:
        parsed = Date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Date must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise ValueError("Date must use YYYY-MM-DD format.")
    return value


IsoDate = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    AfterValidator(_validate_iso_date),
]


class TokenUsageSchema(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0  # Includes billable thinking tokens.
    thinking_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0


class SpecialistResponseSchema(BaseModel):
    """Structured handoff from any specialist to the coordinator."""

    content: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)


class AppointmentAgentResponseSchema(BaseModel):
    """The appointment specialist selects a date; code attaches its slots."""

    content: str = Field(min_length=1)
    # ADK places this model's Python dump into a function response. Keep the
    # value as a validated string so the next coordinator request is JSON-safe.
    agreed_date: IsoDate | None = None


class KnowledgeBaseAgentOutputSchema(SpecialistResponseSchema):
    """Backward-compatible name for older imports."""


class LeadScoreSchema(BaseModel):
    score: int = Field(ge=0, le=100, strict=True)
    summary: str = Field(min_length=1, max_length=240)
    recorded: bool = False


class EscalationSchema(BaseModel):
    requires_attention: bool = True
    escalation_reason: str = Field(min_length=1, max_length=500)


class AgentResultSchema(SpecialistResponseSchema):
    agreed_date: IsoDate | None = None
    lead_score: LeadScoreSchema | None = None
    escalation: EscalationSchema | None = None


class AgentResponseSchema(BaseModel):
    result: AgentResultSchema
    token: TokenUsageSchema = Field(default_factory=TokenUsageSchema)
    cost: float = Field(default=0.0, ge=0)  # Estimated USD.
