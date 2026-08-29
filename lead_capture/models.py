from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Value
from django.db.models.lookups import GreaterThan, LessThanOrEqual

from app.base.models import BaseMinModel, BaseModel
from lead_capture.utils.choices import LeadCaptureFieldMode, LeadStatusType
from lead_capture.utils.validators import (
    MAX_CAPTURE_FIELDS,
    validate_custom_field_config,
    validate_lead_custom_fields,
    total_capture_field_count_expression,
    custom_field_count_expression,
    json_type_expression
)

class LeadCaptureConfig(BaseModel):
    chatbot = models.OneToOneField(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="lead_capture_config",
    )

    is_enabled = models.BooleanField(default=False)

    # fields
    name_mode = models.CharField(
        max_length=10,
        choices=LeadCaptureFieldMode.choices,
        default=LeadCaptureFieldMode.REQUIRED,
    )
    email_mode = models.CharField(
        max_length=10,
        choices=LeadCaptureFieldMode.choices,
        default=LeadCaptureFieldMode.REQUIRED,
    )
    phone_mode = models.CharField(
        max_length=10,
        choices=LeadCaptureFieldMode.choices,
        default=LeadCaptureFieldMode.OPTIONAL,
    )
    address_mode = models.CharField(
        max_length=10,
        choices=LeadCaptureFieldMode.choices,
        default=LeadCaptureFieldMode.HIDDEN,
    )
    custom_fields = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_custom_field_config],
        help_text=(
            "Custom fields as label/value/mode objects. Values must be unique "
            "snake_case keys and modes must be optional or required."
        ),
    )

    # When should the chatbot try collecting information?
    auto_collect = models.BooleanField(
        default=True,
        help_text="Allow the AI to proactively collect lead information.",
    )

    # Optional message shown before asking for information
    intro_message = models.TextField(blank=True, default="")
    require_consent = models.BooleanField(default=False)
    consent_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "lead_capture_config"
        verbose_name = "Lead capture config"
        verbose_name_plural = "Lead Capture Config"
        constraints = [
            models.CheckConstraint(
                condition=json_type_expression("custom_fields", "array"),
                name="lead_capture_custom_fields_array",
            ),
            models.CheckConstraint(
                condition=(
                    Q(is_enabled=False)
                    | ~Q(
                        name_mode=LeadCaptureFieldMode.HIDDEN,
                        email_mode=LeadCaptureFieldMode.HIDDEN,
                        phone_mode=LeadCaptureFieldMode.HIDDEN,
                        address_mode=LeadCaptureFieldMode.HIDDEN,
                    )
                    | GreaterThan(custom_field_count_expression(), Value(0))
                ),
                name="lead_capture_enabled_has_field",
            ),
            models.CheckConstraint(
                condition=LessThanOrEqual(
                    total_capture_field_count_expression(),
                    Value(MAX_CAPTURE_FIELDS),
                ),
                name="lead_capture_max_10_fields",
            ),
        ]

    def clean(self):
        super().clean()
        standard_field_count = sum(
            mode != LeadCaptureFieldMode.HIDDEN
            for mode in (
                self.name_mode,
                self.email_mode,
                self.phone_mode,
                self.address_mode,
            )
        )
        custom_field_count = (
            len(self.custom_fields) if isinstance(self.custom_fields, list) else 0
        )
        total_field_count = standard_field_count + custom_field_count

        if self.is_enabled and total_field_count == 0:
            raise ValidationError(
                "At least one lead field must be optional or required when "
                "lead capture is enabled."
            )
        if total_field_count > MAX_CAPTURE_FIELDS:
            raise ValidationError(
                {
                    "custom_fields": (
                        f"No more than {MAX_CAPTURE_FIELDS} required and "
                        "optional fields can be configured in total."
                    )
                }
            )

    def __str__(self):
        return f"{self.chatbot.chatbot_name}"


class Lead(BaseMinModel):
    """Contact details captured by one chatbot."""

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        related_name="leads",
        on_delete=models.CASCADE,
    )

    name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=200, blank=True)

    # client specified custom fields
    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_lead_custom_fields],
    )

    # ip address
    initial_ip_address = models.GenericIPAddressField(null=True, blank=True)
    last_ip_address = models.GenericIPAddressField(null=True, blank=True)
    detected_country_code = models.CharField(max_length=5, blank=True, default="")
    detected_city = models.CharField(max_length=100, blank=True, default="")

    status = models.CharField(
        max_length=30,
        choices=LeadStatusType.choices,
        default=LeadStatusType.NEW,
        db_index=True,
    )

    # Optional internal AI qualification
    lead_score = models.PositiveSmallIntegerField(null=True, blank=True)

    # Where this lead originally came from
    source = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chatbot", "created_at"],
                name="lead_chatbot_created_idx",
            ),
            models.Index(
                fields=["chatbot", "email"],
                name="lead_chatbot_email_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=json_type_expression("custom_fields", "object"),
                name="lead_custom_fields_object",
            ),
            models.CheckConstraint(
                condition=Q(lead_score__lte=100),
                name="lead_score_between_0_and_100",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        identity = self.name or self.email or self.phone or str(self.id)
        return f"{identity} ({self.chatbot})"


class LeadNote(BaseMinModel):
    lead = models.ForeignKey(
        Lead,
        related_name="notes",
        on_delete=models.CASCADE,
    )
    author = models.ForeignKey(
        "chatbot.ChatbotUser",
        related_name="lead_notes",
        on_delete=models.PROTECT,
    )
    content = models.TextField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["lead", "created_at"],
                name="lead_note_created_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(content=""),
                name="lead_note_content_not_empty",
            ),
        ]

    def clean(self):
        super().clean()
        if self.author_id and not self.author.is_active:
            raise ValidationError(
                {"author": "An inactive chatbot user cannot create notes."}
            )
        if self.lead_id and self.author_id and (
            self.lead.chatbot_id != self.author.chatbot_id
        ):
            raise ValidationError(
                {"author": "The note author must belong to the lead's chatbot."}
            )
        if not self.content.strip():
            raise ValidationError({"content": "Note content cannot be empty."})

    def __str__(self):
        return f"Note on {self.lead} by {self.author.user}"

