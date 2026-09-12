from datetime import date as Date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class TokenUsageSchema(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0  # Includes billable thinking tokens.
    thinking_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0


class KnowledgeBaseAgentOutputSchema(BaseModel):
    content: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)


class AppointmentSchema(BaseModel):
    status: Literal["available", "unavailable", "disabled", "invalid", "booking_recorded"]
    available: bool = False
    requested_date: Date | None = None
    date: Date | None = None
    searched_through: Date | None = None
    next_search_date: Date | None = None
    timezone: str | None = None
    appointment_id: str | None = None
    appointment_status: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class LeadScoreSchema(BaseModel):
    score: int = Field(ge=0, le=100, strict=True)
    summary: str = Field(min_length=1, max_length=240)
    recorded: bool = False


class EscalationSchema(BaseModel):
    requires_attention: bool = True
    escalation_reason: str = Field(min_length=1, max_length=500)


class AgentResultSchema(KnowledgeBaseAgentOutputSchema):
    appointment: AppointmentSchema | None = None
    lead_score: LeadScoreSchema | None = None
    escalation: EscalationSchema | None = None


class AgentResponseSchema(BaseModel):
    result: AgentResultSchema
    token: TokenUsageSchema = Field(default_factory=TokenUsageSchema)
    cost: float = 0.0  # Estimated USD from configured model rates.
