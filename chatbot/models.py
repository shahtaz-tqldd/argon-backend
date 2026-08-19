from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from app.base.models import BaseModel, BaseMinModel
from workspace.models import WorkspaceUser

from chatbot.utils.choices import ChatbotRoleTypes, ChatbotStatusTypes
from chatbot.utils.validation import (
    generate_widget_public_key,
    normalize_widget_origin,
    validate_other_settings,
    validate_widget_settings,
)


class Chatbot(BaseModel):
    workspace = models.ForeignKey(
        "workspace.Workspace",
        on_delete=models.CASCADE,
        related_name="chatbots",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    slug = models.SlugField()
    instructions = models.TextField(blank=True)
    logo = models.URLField(blank=True)

    status = models.CharField(
        max_length=30,
        choices=ChatbotStatusTypes.choices,
        default=ChatbotStatusTypes.DRAFT,
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["workspace__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="unique_chatbot_name_per_workspace",
            ),
        ]
        indexes = [
            models.Index(
                fields=["workspace", "is_active"],
                name="chatbot_workspace_active_idx",
            ),
            models.Index(
                fields=["workspace", "status"],
                name="chatbot_workspace_status_idx",
            ),
        ]

    def __str__(self):
        return self.name


class ChatbotSettings(BaseModel):
    # This model originally used Django's BigAutoField. Keep that database type
    # while inheriting BaseModel's audit fields; PostgreSQL cannot directly cast
    # existing bigint primary-key values to UUIDs.
    id = models.BigAutoField(primary_key=True)

    chatbot = models.OneToOneField(
        Chatbot,
        related_name="settings",
        on_delete=models.CASCADE,
    )

    welcome_message = models.TextField(blank=True)
    fallback_message = models.TextField(blank=True)

    language = models.CharField(max_length=20, default="en")
    ai_enabled = models.BooleanField(default=True)

    # chatbot features
    knowledge_base_enabled = models.BooleanField(
        default=True,
        help_text="Allow answers grounded in the chatbot's knowledge bases.",
    )
    appointment_booking_enabled = models.BooleanField(
        default=False,
        help_text="Allow customers to book appointments or meetings.",
    )
    quotation_generation_enabled = models.BooleanField(
        default=False,
        help_text="Allow the chatbot to prepare pricing and quotations.",
    )
    lead_capture_enabled = models.BooleanField(
        default=False,
        help_text="Allow the chatbot to capture and qualify prospective leads.",
    )
    human_handoff_enabled = models.BooleanField(
        default=False,
        help_text="Allow the chatbot to handoff to the human assistant.",
    )

    # We will not prioritize this now
    order_taking_enabled = models.BooleanField(
        default=False,
        help_text="Allow the chatbot to collect and submit customer orders.",
    )

    # chatbot widget
    widget_public_key = models.CharField(
        max_length=64,
        unique=True,
        default=generate_widget_public_key,
        editable=False,
        help_text="Public key embedded in the generated widget script.",
    )
    widget_enabled = models.BooleanField(default=True)
    widget_settings = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_widget_settings],
        help_text=(
            "Widget presentation settings such as colors, text, position, and "
            "launcher appearance."
        ),
    )

    # other settings
    other_settings = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_other_settings],
        help_text="Miscellaneous settings to operate chatbot",
    )

    def __str__(self):
        return f"Settings for {self.chatbot}"


class ChatbotAllowedOrigin(BaseModel):
    chatbot = models.ForeignKey(
        Chatbot,
        related_name="allowed_origins",
        on_delete=models.CASCADE,
    )
    origin = models.CharField(
        max_length=300,
        help_text="Allowed widget origin, for example https://www.example.com.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["origin"]
        constraints = [
            models.UniqueConstraint(
                fields=["chatbot", "origin"],
                name="unique_origin_per_chatbot",
            ),
        ]
        indexes = [
            models.Index(
                fields=["chatbot", "is_active"],
                name="chatbot_origin_active_idx",
            ),
        ]

    def clean(self):
        super().clean()
        self.origin = normalize_widget_origin(self.origin)

    def save(self, *args, **kwargs):
        self.origin = normalize_widget_origin(self.origin)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.origin} -> {self.chatbot}"


class ChatbotUser(BaseMinModel):
    chatbot = models.ForeignKey(
        Chatbot,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chatbot_memberships",
    )
    role = models.CharField(
        max_length=12,
        choices=ChatbotRoleTypes.choices,
        default=ChatbotRoleTypes.MEMBER,
    )

    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["chatbot__name", "user__email"]
        constraints = [
            models.UniqueConstraint(
                fields=["chatbot", "user"],
                name="unique_user_per_chatbot",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "is_active"],
                name="chatbot_user_active_idx",
            ),
            models.Index(
                fields=["chatbot", "role", "is_active"],
                name="chatbot_role_active_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.chatbot_id
            and self.user_id
            and not WorkspaceUser.objects.filter(
                workspace_id=self.chatbot.workspace_id,
                user_id=self.user_id,
                is_active=True,
            ).exists()
        ):
            raise ValidationError(
                "A chatbot user must be an active member of its workspace."
            )

    def __str__(self):
        return f"{self.user} in {self.chatbot} ({self.get_role_display()})"
