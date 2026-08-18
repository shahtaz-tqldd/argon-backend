from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from app.base.models import BaseModel


class NotificationType(models.TextChoices):
    GENERAL = "general", "General"
    TRIP = "trip", "Trip"
    GLOBAL = "global", "Global"


class Notification(BaseModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
        help_text="Empty only for global notifications.",
    )
    trip = models.ForeignKey(
        "trips.Trip",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
    )
    notification_type = models.CharField(
        max_length=20,
        choices=NotificationType.choices,
        db_index=True,
    )
    title = models.CharField(max_length=180)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "notification_type", "-created_at"]),
            models.Index(fields=["recipient", "trip", "-created_at"]),
            models.Index(fields=["notification_type", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(notification_type=NotificationType.GLOBAL, recipient__isnull=True)
                    | ~models.Q(notification_type=NotificationType.GLOBAL)
                ),
                name="global_notifications_have_no_recipient",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(notification_type=NotificationType.GLOBAL)
                    | models.Q(recipient__isnull=False)
                ),
                name="direct_notifications_have_recipient",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(notification_type=NotificationType.TRIP, trip__isnull=False)
                    | ~models.Q(notification_type=NotificationType.TRIP)
                ),
                name="trip_notifications_have_trip",
            ),
        ]

    def clean(self):
        if self.notification_type == NotificationType.GLOBAL and self.recipient_id:
            raise ValidationError("Global notifications cannot have a recipient.")
        if self.notification_type != NotificationType.GLOBAL and not self.recipient_id:
            raise ValidationError("General and trip notifications require a recipient.")
        if self.notification_type == NotificationType.TRIP and not self.trip_id:
            raise ValidationError("Trip notifications require a trip.")

    def __str__(self):
        target = "all users" if self.notification_type == NotificationType.GLOBAL else self.recipient
        return f"{self.title} -> {target}"


class NotificationRead(BaseModel):
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="read_receipts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_reads",
        db_index=True,
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-read_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "user"],
                name="unique_notification_read_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self):
        return f"{self.user} read {self.notification_id}"
