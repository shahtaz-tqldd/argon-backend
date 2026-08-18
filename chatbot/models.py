from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from app.base.models import BaseModel
from workspace.models import WorkspaceUser


class ChatbotRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


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
        choices=[
            ("draft", "Draft"),
            ("active", "Active"),
            ("disabled", "Disabled"),
        ],
        default="draft",
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
        ]

    def __str__(self):
        return self.name


class ChatbotSettings(models.Model):
    chatbot = models.OneToOneField(
        Chatbot,
        related_name="settings",
        on_delete=models.CASCADE,
    )

    welcome_message = models.TextField(blank=True)
    fallback_message = models.TextField(blank=True)

    language = models.CharField(
        max_length=20,
        default="en",
    )

    collect_user_info = models.BooleanField(default=False)
    human_handoff_enabled = models.BooleanField(default=False)
    ai_enabled = models.BooleanField(default=True)


class ChatbotUser(BaseModel):
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
        choices=ChatbotRole.choices,
        default=ChatbotRole.MEMBER,
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
