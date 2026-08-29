from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from app.base.models import BaseModel, BaseMinModel
from app.utils.validators import validate_timezone_name

from chatbot.utils.choices import (
    ChatbotPermissionTypes,
    ChatbotRoleTypes,
    ChatbotStatusTypes,
    ChatbotWidgetLauncherPositionTypes,
    ChatbotWidgetThemeTypes,
)
from chatbot.utils.permissions import (
    default_chatbot_user_permissions,
    effective_chatbot_permission_codes,
)
from chatbot.utils.validation import (
    generate_widget_public_key,
    normalize_widget_origin,
    validate_hex_color,
    validate_other_settings,
    validate_widget_settings,
)
from subscription.choices import PlanFeature


DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE = (
    "Hey, I am {chatbot_name}, I am here to answer anything you want to know "
    "about {business_name}."
)
DEFAULT_CHATBOT_FALLBACK_MESSAGE = (
    "Sorry, I couldn't find anything to my knowledge to answer this question, "
    "should I connect with you one of our human assistant?"
)
DEFAULT_CHATBOT_ESCALATION_RULE = (
    "Hand off to human agent, when you don't find any answer, asking about "
    "payment or collaboration."
)
DEFAULT_CHATBOT_NEVER_ANSWER = (
    "Never answer about payment, outside scope and all."
)
DEFAULT_WIDGET_HEADER_TITLE_TEMPLATE = "{chatbot_name}"
DEFAULT_WIDGET_HEADER_DESCRIPTION = "typically replies instantly"


def build_default_chatbot_welcome_message(chatbot_name, business_name=""):
    return DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE.format(
        chatbot_name=chatbot_name,
        business_name=business_name,
    )


class Chatbot(BaseModel):
    workspace = models.ForeignKey(
        "workspace.Workspace",
        on_delete=models.CASCADE,
        related_name="chatbots",
    )

    # identity
    chatbot_name = models.CharField(max_length=120)
    business_name = models.CharField(max_length=120, blank=True, default="")
    description = models.TextField(blank=True)
    slug = models.SlugField(
        max_length=140,
        unique=True,
        blank=True,
        editable=False,
    )
    logo = models.URLField(blank=True)

    # conversation
    welcome_message = models.TextField(
        blank=True,
        default=DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE,
    )
    fallback_message = models.TextField(
        blank=True,
        default=DEFAULT_CHATBOT_FALLBACK_MESSAGE,
    )
    instructions = models.TextField(blank=True)
    escalation_rule = models.TextField(
        blank=True,
        default=DEFAULT_CHATBOT_ESCALATION_RULE,
    )
    never_answer = models.TextField(
        blank=True,
        default=DEFAULT_CHATBOT_NEVER_ANSWER,
    )

    language = models.CharField(max_length=20, default="en")
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        validators=[validate_timezone_name],
        help_text="IANA timezone, for example Asia/Dhaka.",
    )

    # core features
    ai_enabled = models.BooleanField(default=True)
    knowledge_base_enabled = models.BooleanField(
        default=True,
        help_text="Allow answers grounded in the chatbot's knowledge bases.",
    )
    human_handoff_enabled = models.BooleanField(
        default=True,
        help_text="Allow the chatbot to handoff to a human assistant.",
    )
    # other settings
    other_settings = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_other_settings],
        help_text="Miscellaneous settings to operate chatbot",
    )

    # lifecycle
    status = models.CharField(
        max_length=30,
        choices=ChatbotStatusTypes.choices,
        default=ChatbotStatusTypes.DRAFT,
        db_index=True,
    )

    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["workspace__name", "chatbot_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "chatbot_name"],
                name="unique_chatbot_name_per_workspace",
            ),
        ]

        indexes = [
            models.Index(
                fields=["workspace", "is_deleted"],
                name="chatbot_workspace_active_idx",
            ),
            models.Index(
                fields=["workspace", "status"],
                name="chatbot_workspace_status_idx",
            ),
        ]

    def __str__(self):
        return self.chatbot_name

    def generate_unique_slug(self):
        base_slug = (
            slugify(self.chatbot_name)[:120].strip("-") or "chatbot"
        )

        queryset = type(self).objects.all()

        if self.pk:
            queryset = queryset.exclude(pk=self.pk)

        candidate = base_slug
        suffix = 2

        while queryset.filter(slug=candidate).exists():
            suffix_text = f"-{suffix}"
            candidate = (
                f"{base_slug[:140 - len(suffix_text)]}"
                f"{suffix_text}"
            )
            suffix += 1

        return candidate

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.generate_unique_slug()
        if self.welcome_message == DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE:
            self.welcome_message = build_default_chatbot_welcome_message(
                self.chatbot_name,
                self.business_name,
            )

        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return (
            not self.is_deleted
            and self.status
            not in {
                ChatbotStatusTypes.DISABLED,
                ChatbotStatusTypes.DISABLED_BY_ADMIN,
            }
        )


class ChatbotWidgetSettings(BaseModel):
    chatbot = models.OneToOneField(
        Chatbot,
        related_name="widget_settings",
        on_delete=models.CASCADE,
    )

    is_enabled = models.BooleanField(default=True)
    public_key = models.CharField(
        max_length=64,
        unique=True,
        default=generate_widget_public_key,
        editable=False,
    )

    # appearance
    primary_color = models.CharField(
        max_length=9,
        default="#3a86ff",
        validators=[validate_hex_color],
    )
    secondary_color = models.CharField(
        max_length=9,
        default="#fafafa",
        validators=[validate_hex_color],
    )

    launcher_position = models.CharField(
        max_length=30,
        choices=ChatbotWidgetLauncherPositionTypes.choices,
        default=ChatbotWidgetLauncherPositionTypes.BOTTOM_RIGHT,
    )
    launcher_text = models.CharField(max_length=100, blank=True)

    # widget behaviour
    header_title = models.CharField(
        max_length=60,
        blank=True,
        default=DEFAULT_WIDGET_HEADER_TITLE_TEMPLATE,
    )
    header_description = models.CharField(
        max_length=100,
        blank=True,
        default=DEFAULT_WIDGET_HEADER_DESCRIPTION,
    )
    show_branding = models.BooleanField(default=True)
    theme = models.CharField(
        max_length=20,
        choices=ChatbotWidgetThemeTypes.choices,
        default=ChatbotWidgetThemeTypes.LIGHT,
    )

    # Keep highly variable UI configuration here
    other_settings = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_widget_settings],
    )

    def save(self, *args, **kwargs):
        if self.header_title == DEFAULT_WIDGET_HEADER_TITLE_TEMPLATE:
            self.header_title = self.chatbot.chatbot_name[:60]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Widget settings: {self.chatbot}"


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
    permissions = models.JSONField(
        default=default_chatbot_user_permissions,
        blank=True,
        help_text="Permission codes explicitly granted to this chatbot member.",
    )

    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["chatbot__chatbot_name", "user__email"]
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
        if not isinstance(self.permissions, list):
            raise ValidationError(
                {"permissions": "Permissions must be a list of permission codes."}
            )
        if not all(isinstance(permission, str) for permission in self.permissions):
            raise ValidationError(
                {"permissions": "Every permission code must be a string."}
            )
        unknown_permissions = set(self.permissions) - set(
            ChatbotPermissionTypes.values
        )
        if unknown_permissions:
            raise ValidationError(
                {
                    "permissions": (
                        "Unknown permission codes: "
                        f"{', '.join(sorted(unknown_permissions))}."
                    )
                }
            )

    def effective_permissions(self):
        if not self.is_active:
            return []
        return effective_chatbot_permission_codes(self)

    @property
    def all_permissions(self):
        return self.role == ChatbotRoleTypes.ADMIN

    def has_permission(self, permission):
        try:
            permission = ChatbotPermissionTypes(permission).value
        except ValueError:
            return False
        return permission in self.effective_permissions()

    def __str__(self):
        return f"{self.user} in {self.chatbot} ({self.get_role_display()})"


class ChatbotInvitation(BaseModel):
    chatbot = models.ForeignKey(
        Chatbot,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    invited_at = models.DateTimeField(default=timezone.now, db_index=True)
    permissions = models.JSONField(
        default=default_chatbot_user_permissions,
        blank=True,
        help_text="Permissions to grant when this invitation is accepted.",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["chatbot", "email"],
                name="unique_chatbot_invitation_email",
            ),
        ]
        indexes = [
            models.Index(
                fields=["chatbot", "expires_at"],
                name="chatbot_invite_expiry_idx",
            ),
        ]

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def is_accepted(self):
        return self.accepted_at is not None

    def save(self, *args, **kwargs):
        self.email = self.email.strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invitation for {self.email} to {self.chatbot}"


class ChatbotCapacity(BaseMinModel):
    """Pre-calculated subscription limits and usage for one chatbot."""

    chatbot = models.OneToOneField(
        Chatbot,
        related_name="capacity",
        on_delete=models.CASCADE,
    )

    ai_message_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum AI messages for the period; null means unlimited.",
    )
    current_ai_message_count = models.PositiveIntegerField(default=0)

    file_size_limit_bytes = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text="Maximum stored knowledge bytes; null means unlimited.",
    )
    current_file_size_bytes = models.PositiveBigIntegerField(default=0)

    knowledge_chunk_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum knowledge chunks; null means unlimited.",
    )
    current_knowledge_chunk_count = models.PositiveIntegerField(default=0)

    active_features = ArrayField(
        base_field=models.CharField(
            max_length=40,
            choices=PlanFeature.choices,
        ),
        default=list,
        blank=True,
    )

    class Meta:
        verbose_name = "Chatbot capacity"
        verbose_name_plural = "Chatbot capacities"

    def clean(self):
        super().clean()
        features = self.active_features or []
        if len(features) != len(set(features)):
            raise ValidationError(
                {"active_features": "Active features must be unique."}
            )

    def has_feature(self, feature):
        feature = getattr(feature, "value", feature)
        return feature in (self.active_features or [])

    def __str__(self):
        return f"Capacity: {self.chatbot}"
