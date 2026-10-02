from datetime import date as Date

from pydantic import BaseModel, Field


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
    agreed_date: Date | None = None


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
    agreed_date: Date | None = None
    lead_score: LeadScoreSchema | None = None
    escalation: EscalationSchema | None = None


class AgentResponseSchema(BaseModel):
    result: AgentResultSchema
    token: TokenUsageSchema = Field(default_factory=TokenUsageSchema)
    cost: float = Field(default=0.0, ge=0)  # Estimated USD.
