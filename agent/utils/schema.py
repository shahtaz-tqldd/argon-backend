from pydantic import BaseModel, Field


class ConversationAnalysis(BaseModel):
    summary: str = Field(description="Concise factual summary of the conversation.")
    lead_score: int = Field(ge=0, le=100, description="Commercial intent score, 0 to 100.")
    lead_score_reason: str
    next_steps: list[str] = Field(default_factory=list)
