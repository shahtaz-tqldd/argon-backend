from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.text import slugify

from app.core.models import BaseModel, BaseMinModel
from app.utils.validators import validate_timezone_name

from chatbot.utils.choices import (
    ChatbotActivityModuleTypes,
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


DEFAULT_TEST_AI_MESSAGE_LIMIT = 100
BYTES_PER_MEGABYTE = 1024 * 1024
_UNSET_SUBSCRIPTION = object()


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
MAX_ALLOWED_ORIGINS_PER_CHATBOT = 5


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
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                return
            kwargs["update_fields"] = update_fields

        previous = None
        database = kwargs.get("using") or self._state.db
        if not self._state.adding and (
            update_fields is None
            or {"chatbot_name", "business_name"} & update_fields
        ):
            previous = type(self).objects.using(database).filter(pk=self.pk).values(
                "chatbot_name", "business_name", "welcome_message",
            ).first()

        if not self.slug:
            self.slug = self.generate_unique_slug()
        default_welcome = self.welcome_message == DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE
        name_changed = False
        chatbot_name = self.chatbot_name
        business_name = self.business_name
        if previous is not None:
            if update_fields is not None:
                if "chatbot_name" not in update_fields:
                    chatbot_name = previous["chatbot_name"]
                if "business_name" not in update_fields:
                    business_name = previous["business_name"]
            name_changed = chatbot_name != previous["chatbot_name"]
            identity_changed = name_changed or business_name != previous["business_name"]
            default_welcome = default_welcome or (
                identity_changed
                and self.welcome_message == previous["welcome_message"]
                and previous["welcome_message"] == build_default_chatbot_welcome_message(
                    previous["chatbot_name"], previous["business_name"],
                )
            )
        if default_welcome:
            self.welcome_message = build_default_chatbot_welcome_message(
                chatbot_name, business_name,
            )
            if update_fields is not None:
                update_fields.add("welcome_message")

        with transaction.atomic(using=database):
            super().save(*args, **kwargs)
            if name_changed:
                ChatbotWidgetSettings.objects.using(database).filter(
                    chatbot_id=self.pk,
                    header_title__in=[
                        previous["chatbot_name"][:60],
                        DEFAULT_WIDGET_HEADER_TITLE_TEMPLATE,
                    ],
                ).update(
                    header_title=DEFAULT_WIDGET_HEADER_TITLE_TEMPLATE.format(
                        chatbot_name=chatbot_name,
                    )[:60],
                    updated_at=timezone.now(),
                    updated_by_id=self.updated_by_id,
                )
                self._state.fields_cache.pop("widget_settings", None)

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

    @property
    def appointment_booking_enabled(self):
        config = getattr(self, "appointment_booking_config", None)
        return bool(config and config.is_enabled)


class ChatbotConfig(BaseMinModel):
    """Usage counters for one chatbot.

    Limits and features are not stored here; they are derived from the
    chatbot's current subscription snapshot on demand.
    """

    chatbot = models.OneToOneField(
        Chatbot,
        related_name="capacity",
        on_delete=models.CASCADE,
    )

    current_ai_message_count = models.PositiveIntegerField(default=0)
    current_test_ai_message_count = models.PositiveIntegerField(default=0)
    current_file_size_bytes = models.PositiveBigIntegerField(default=0)
    current_knowledge_chunk_count = models.PositiveIntegerField(default=0)

    _active_subscription = _UNSET_SUBSCRIPTION

    class Meta:
        verbose_name = "Chatbot config"
        verbose_name_plural = "Chatbot configs"

    def active_subscription(self):
        """Return the chatbot's open subscription, or None.

        The result is cached on the instance so limit and feature lookups
        share a single query.
        """
        from subscription.models import ChatbotSubscription
        from subscription.services.subscriptions import OPEN_SUBSCRIPTION_STATUSES

        if self._active_subscription is _UNSET_SUBSCRIPTION:
            self._active_subscription = ChatbotSubscription.objects.filter(
                chatbot_id=self.chatbot_id,
                status__in=OPEN_SUBSCRIPTION_STATUSES,
            ).first()
        return self._active_subscription

    @property
    def ai_message_limit(self):
        """Maximum AI messages for the period; None means unlimited."""
        subscription = self.active_subscription()
        return subscription.get_ai_message_limit() if subscription else None

    @property
    def test_ai_message_limit(self):
        """Free AI replies reserved for test sessions."""
        return DEFAULT_TEST_AI_MESSAGE_LIMIT

    @property
    def file_size_limit_bytes(self):
        """Maximum stored knowledge bytes; None means unlimited."""
        subscription = self.active_subscription()
        if subscription is None:
            return None
        file_size_limit_mb = subscription.get_file_size_limit_mb()
        if file_size_limit_mb is None:
            return None
        return file_size_limit_mb * BYTES_PER_MEGABYTE

    @property
    def knowledge_chunk_limit(self):
        """Maximum knowledge chunks; None means unlimited."""
        subscription = self.active_subscription()
        return subscription.get_knowledge_chunk_limit() if subscription else None

    @property
    def active_features(self):
        """Plan features enabled by the current subscription."""
        subscription = self.active_subscription()
        return subscription.get_features() if subscription else []

    def has_feature(self, feature):
        subscription = self.active_subscription()
        return bool(subscription and subscription.has_feature(feature))

    def current_subscription(self):
        """Return snapshot pricing and the live next billing date, or None.

        The contract already snapshots plan details on ChatbotSubscription.
        Billing dates remain live so renewals and cancellations are reflected.
        """
        from subscription.choices import RenewalMode

        subscription = self.active_subscription()
        if subscription is None:
            return None

        next_billing_at = None
        if not subscription.is_free_plan() and not subscription.cancel_at_period_end:
            next_billing_at = subscription.next_billing_at
            if (
                next_billing_at is None
                and subscription.renewal_mode == RenewalMode.PROVIDER_MANAGED
            ):
                next_billing_at = subscription.current_period_end

        return {
            "plan_name": subscription.get_plan_name(),
            "billing_cycle": subscription.get_billing_interval(),
            "price": subscription.get_price_amount(),
            "currency": subscription.get_currency(),
            "next_billing_at": next_billing_at,
        }

    def __str__(self):
        return f"Config: {self.chatbot}"


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
        if not self._state.adding:
            return super().save(*args, **kwargs)

        # Serialize origin creation per chatbot so concurrent requests cannot
        # both pass the limit check.
        with transaction.atomic():
            Chatbot.objects.select_for_update().get(pk=self.chatbot_id)
            if (
                ChatbotAllowedOrigin.objects.filter(
                    chatbot_id=self.chatbot_id,
                ).count()
                >= MAX_ALLOWED_ORIGINS_PER_CHATBOT
            ):
                raise ValidationError(
                    {
                        "origin": (
                            "A chatbot can have at most "
                            f"{MAX_ALLOWED_ORIGINS_PER_CHATBOT} allowed origins."
                        )
                    }
                )
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.origin} -> {self.chatbot}"


# CHATBOT USER

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


# CHATBOT ACTIVITY

class ChatbotActivityLog(BaseMinModel):
    """A record of an action performed inside a chatbot."""

    chatbot = models.ForeignKey(
        Chatbot,
        on_delete=models.CASCADE,
        related_name="activity_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chatbot_activity_logs",
        help_text="The user who performed the action; null denotes the system.",
    )
    module = models.CharField(
        max_length=20,
        choices=ChatbotActivityModuleTypes.choices,
        default=ChatbotActivityModuleTypes.OTHER,
        db_index=True,
    )
    action = models.CharField(max_length=100, db_index=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["chatbot", "-created_at"],
                name="chatbot_activity_time_idx",
            ),
            models.Index(
                fields=["chatbot", "user", "-created_at"],
                name="chatbot_user_activity_idx",
            ),
        ]

    def clean(self):
        super().clean()
        self.action = self.action.strip()
        if not self.action:
            raise ValidationError({"action": "Action is required."})
        if not isinstance(self.metadata, dict):
            raise ValidationError({"metadata": "Metadata must be an object."})

    def __str__(self):
        actor = self.user or "System"
        return f"{actor}: {self.action} in {self.chatbot}"

