from django.db import models

from app.base.models import BaseMinModel
from analytics.choices import AIUsageType
from analytics.validators import validate_ai_usage_metadata


class AIUsage(BaseMinModel):
    user = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_usages",
        db_index=True,
    )

    trip = models.ForeignKey(
        "trips.Trip",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_usages",
    )

    usage_type = models.CharField(
        max_length=30,
        choices=AIUsageType.choices,
        db_index=True,
    )

    cost = models.DecimalField(
        max_digits=12,
        decimal_places=8,
        default=0,
    )

    tokens = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_ai_usage_metadata],
    )
