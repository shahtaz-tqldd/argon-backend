from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models import Q
from django.utils.dateparse import parse_date

from app.base.models import BaseMinModel, BaseModel
from lead_capture.utils.choices import LeadCaptureFieldMode, LeadStatusType
from lead_capture.utils.validators import (
    MAX_CAPTURE_FIELDS,
    default_collectable_fields,
    json_type_expression,
    validate_collectable_fields,
    validate_collected_fields,
)


class LeadCaptureConfig(BaseModel):
    chatbot = models.OneToOneField(
        "chatbot.Chatbot",
        on_delete=models.CASCADE,
        related_name="lead_capture_config",
    )

    is_enabled = models.BooleanField(default=False)

    collectable_fields = models.JSONField(
        default=default_collectable_fields,
        validators=[validate_collectable_fields],
        help_text=(
            "Lead fields as label/value/mode/type objects. Name, email, phone, "
            "and address are provided by default."
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
                condition=json_type_expression("collectable_fields", "array"),
                name="lead_capture_fields_array",
            ),
        ]

    def clean(self):
        super().clean()
        fields = (
            self.collectable_fields
            if isinstance(self.collectable_fields, list)
            else []
        )
        visible_fields = [
            field
            for field in fields
            if isinstance(field, dict)
            and field.get("mode") != LeadCaptureFieldMode.HIDDEN
        ]
        if self.is_enabled and not visible_fields:
            raise ValidationError(
                "At least one lead field must be optional or required when "
                "lead capture is enabled."
            )
        if len(visible_fields) > MAX_CAPTURE_FIELDS:
            raise ValidationError(
                {
                    "collectable_fields": (
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

    collected_fields = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_collected_fields],
        help_text="Values collected using the chatbot's lead configuration.",
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
        ]
        constraints = [
            models.CheckConstraint(
                condition=json_type_expression("collected_fields", "object"),
                name="lead_collected_fields_object",
            ),
            models.CheckConstraint(
                condition=Q(lead_score__lte=100),
                name="lead_score_between_0_and_100",
            ),
        ]

    def clean(self):
        super().clean()
        self._validate_collected_fields_against_config()

    def _validate_collected_fields_against_config(self):
        if not self.chatbot_id or not isinstance(self.collected_fields, dict):
            return
        try:
            config = LeadCaptureConfig.objects.get(chatbot_id=self.chatbot_id)
        except LeadCaptureConfig.DoesNotExist:
            raise ValidationError(
                {
                    "collected_fields": (
                        "The chatbot has no lead capture configuration."
                    )
                }
            )

        configured_fields = {
            field["value"]: field
            for field in config.collectable_fields
            if isinstance(field, dict) and isinstance(field.get("value"), str)
        }
        visible_fields = {
            value: field
            for value, field in configured_fields.items()
            if field.get("mode") != LeadCaptureFieldMode.HIDDEN
        }
        unknown_fields = set(self.collected_fields) - set(visible_fields)
        if unknown_fields:
            raise ValidationError(
                {
                    "collected_fields": (
                        "Fields are not collectable for this configuration: "
                        f"{', '.join(sorted(unknown_fields))}."
                    )
                }
            )
        missing_fields = [
            value
            for value, field in visible_fields.items()
            if field.get("mode") == LeadCaptureFieldMode.REQUIRED
            and self._is_empty_value(self.collected_fields.get(value))
        ]
        if missing_fields:
            raise ValidationError(
                {
                    "collected_fields": (
                        "Required fields are missing: "
                        f"{', '.join(sorted(missing_fields))}."
                    )
                }
            )
        for value, collected_value in self.collected_fields.items():
            if self._is_empty_value(collected_value):
                continue
            field_type = visible_fields[value].get("type")
            if field_type == "text" and not isinstance(collected_value, str):
                raise ValidationError(
                    {"collected_fields": f"'{value}' must be text."}
                )
            if field_type == "email":
                if not isinstance(collected_value, str):
                    raise ValidationError(
                        {"collected_fields": f"'{value}' must be an email."}
                    )
                try:
                    validate_email(collected_value)
                except ValidationError:
                    raise ValidationError(
                        {
                            "collected_fields": (
                                f"'{value}' must be a valid email."
                            )
                        }
                    )
            if field_type == "date" and (
                not isinstance(collected_value, str)
                or parse_date(collected_value) is None
            ):
                raise ValidationError(
                    {
                        "collected_fields": (
                            f"'{value}' must be a date in YYYY-MM-DD format."
                        )
                    }
                )

    @staticmethod
    def _is_empty_value(value):
        return value is None or (isinstance(value, str) and not value.strip())

    def save(self, *args, **kwargs):
        if isinstance(self.collected_fields, dict):
            self.collected_fields = dict(self.collected_fields)
            email = self.collected_fields.get("email")
            if isinstance(email, str):
                self.collected_fields["email"] = email.strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        identity = next(
            (
                self.collected_fields.get(field)
                for field in ("name", "email", "phone")
                if self.collected_fields.get(field)
            ),
            str(self.id),
        )
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
