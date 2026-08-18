from django.db import models


class AIUsageType(models.TextChoices):
    CHAT = "chat", "Chat"
    TRIP_CHAT = "trip_chat", "Trip Chat"
    TRIP_PLANNING = "trip_planning", "Trip Planning"
