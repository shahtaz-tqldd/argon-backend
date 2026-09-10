from django.db import models


class AIUsageType(models.TextChoices):
    CHAT = "chat", "Chat"
    CONTENT_GENERATION = "content_generation", "Content generation"
