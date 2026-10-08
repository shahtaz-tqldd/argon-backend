from django.db import models


class AIUsageType(models.TextChoices):
    CHAT = "chat", "Chat"
    LEAD_INSIGHT_GENERATION = "lead_insight_generation", "Lead Insight Generation"
    CONTENT_GENERATION = "content_generation", "Content generation"
