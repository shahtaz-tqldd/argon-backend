from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from app.services.r2 import delete_image, schedule_delete_image, upload_image
from app.utils.storage_fields import R2ImageField
from chatbot.models import (
    Chatbot,
    ChatbotAllowedOrigin,
    ChatbotInvitation,
    ChatbotUser,
    ChatbotWidgetSettings,
)
from chatbot.services.invitations import (
    InvalidChatbotInvitation,
    accept_chatbot_invitation,
    get_valid_chatbot_invitation,
    issue_chatbot_invitation,
)
from chatbot.services.membership import create_chatbot
from chatbot.utils.choices import ChatbotPermissionTypes
from chatbot.utils.permissions import (
    default_chatbot_user_permissions,
    normalize_chatbot_permission_codes,
)
from chatbot.utils.validation import (
    normalize_widget_origin,
    validate_unique_chatbot_name,
)
from workspace.models import Workspace, WorkspaceUser

User = get_user_model()


class ChatbotQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()


class ChatbotMemberQuerySerializer(ChatbotQuerySerializer):
    member_email = serializers.EmailField(max_length=254)

    def validate_member_email(self, value):
        return User.objects.normalize_email(value).strip().casefold()


class ChatbotWorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ("id", "name", "slug")
        read_only_fields = fields


class WorkspaceReferenceField(serializers.RelatedField):
    """Accept a workspace UUID or slug and return its compact representation."""

    def to_internal_value(self, data):
        value = str(data).strip()
        workspace = self.get_queryset().filter(slug=value).first()
        if workspace is None:
            try:
                workspace_id = UUID(value)
            except (TypeError, ValueError, AttributeError):
                workspace_id = None
            if workspace_id is not None:
                workspace = self.get_queryset().filter(pk=workspace_id).first()
        if workspace is None:
            raise serializers.ValidationError("Workspace not found or inactive.")
        return workspace

    def to_representation(self, value):
        return ChatbotWorkspaceSerializer(value).data


class ChatbotBaseSerializer(serializers.ModelSerializer):
    logo = R2ImageField(required=False)
    clear_logo = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
    )
    workspace = WorkspaceReferenceField(
        queryset=Workspace.objects.filter(is_active=True),
        required=False,
    )
    workspace_id = serializers.UUIDField(write_only=True, required=False)
    workspace_slug = serializers.SlugField(write_only=True, required=False)
    member_count = serializers.SerializerMethodField()
    current_user_role = serializers.SerializerMethodField()

    class Meta:
        model = Chatbot
        fields = (
            "id",
            "workspace",
            "workspace_id",
            "workspace_slug",
            "chatbot_name",
            "business_name",
            "description",
            "slug",
            "welcome_message",
            "fallback_message",
            "instructions",
            "escalation_rule",
            "never_answer",
            "language",
            "timezone",
            "ai_enabled",
            "knowledge_base_enabled",
            "human_handoff_enabled",
            "other_settings",
            "logo",
            "clear_logo",
            "status",
            "member_count",
            "current_user_role",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "slug",
            "member_count",
            "current_user_role",
            "created_at",
            "updated_at",
        )
        validators = []

    def get_member_count(self, obj):
        return obj.memberships.filter(is_active=True).count()

    def get_current_user_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        membership = obj.memberships.filter(
            user=request.user,
            is_active=True,
        ).first()
        return membership.role if membership else None

    def validate(self, attrs):
        if attrs.get("logo") and attrs.get("clear_logo"):
            raise serializers.ValidationError(
                {"clear_logo": "Cannot clear and replace the logo together."}
            )

        workspace = attrs.pop("workspace", None)
        workspace_id = attrs.pop("workspace_id", None)
        workspace_slug = attrs.pop("workspace_slug", None)

        if self.instance is not None:
            if workspace is not None or workspace_id or workspace_slug:
                raise serializers.ValidationError(
                    {"workspace": "A chatbot cannot be moved to another workspace."}
                )
            workspace = self.instance.workspace
        else:
            if workspace is None and not workspace_id and not workspace_slug:
                raise serializers.ValidationError(
                    {
                        "workspace": (
                            "workspace, workspace_id, or workspace_slug is required."
                        )
                    }
                )
            if workspace_id or workspace_slug:
                workspace_query = Workspace.objects.filter(is_active=True)
                if workspace_id:
                    workspace_query = workspace_query.filter(pk=workspace_id)
                if workspace_slug:
                    workspace_query = workspace_query.filter(slug=workspace_slug)
                referenced_workspace = workspace_query.first()
                if referenced_workspace is None:
                    raise serializers.ValidationError(
                        {"workspace": "Workspace not found or inactive."}
                    )
                if workspace is not None and workspace.pk != referenced_workspace.pk:
                    raise serializers.ValidationError(
                        {"workspace": "Workspace references do not match."}
                    )
                workspace = referenced_workspace
            request = self.context["request"]
            if not WorkspaceUser.objects.filter(
                workspace=workspace,
                user=request.user,
                is_active=True,
            ).exists():
                raise serializers.ValidationError(
                    {"workspace": "You are not an active member of this workspace."}
                )
            attrs["workspace"] = workspace

        if "chatbot_name" in attrs:
            chatbot_name = attrs["chatbot_name"].strip()
            if not chatbot_name:
                raise serializers.ValidationError(
                    {"chatbot_name": "This field is required."}
                )
            try:
                validate_unique_chatbot_name(
                    workspace=workspace,
                    chatbot_name=chatbot_name,
                    chatbot_id=self.instance.pk if self.instance else None,
                )
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {"chatbot_name": exc.messages[0]}
                ) from exc
            attrs["chatbot_name"] = chatbot_name
        return attrs

    def create(self, validated_data):
        workspace = validated_data.pop("workspace")
        logo = validated_data.pop("logo", None)
        validated_data.pop("clear_logo", False)
        upload = None
        if logo is not None:
            upload = upload_image(
                logo,
                folder=f"{settings.R2_IMAGES_PREFIX}/chatbots",
                public_id=f"chatbot-{uuid4().hex}",
            )
            validated_data["logo"] = upload["url"]

        try:
            return create_chatbot(
                workspace=workspace,
                created_by=self.context["request"].user,
                **validated_data,
            )
        except (DjangoValidationError, ValueError) as exc:
            if upload is not None:
                delete_image(public_id=upload["key"])
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            raise serializers.ValidationError({"non_field_errors": message}) from exc
        except Exception:
            if upload is not None:
                delete_image(public_id=upload["key"])
            raise

    def update(self, instance, validated_data):
        logo = validated_data.pop("logo", None)
        clear_logo = validated_data.pop("clear_logo", False)
        previous_logo_url = instance.logo
        upload = None

        if logo is not None:
            upload = upload_image(
                logo,
                folder=f"{settings.R2_IMAGES_PREFIX}/chatbots",
                public_id=f"chatbot-{instance.pk}-{uuid4().hex}",
            )
            validated_data["logo"] = upload["url"]
        elif clear_logo:
            validated_data["logo"] = ""

        instance.updated_by = self.context["request"].user
        try:
            with transaction.atomic():
                for field, value in validated_data.items():
                    setattr(instance, field, value)
                update_fields = [*validated_data.keys(), "updated_by", "updated_at"]
                instance.save(update_fields=update_fields)
        except Exception:
            if upload is not None:
                delete_image(public_id=upload["key"])
            raise

        if previous_logo_url and previous_logo_url != instance.logo:
            schedule_delete_image(image_url=previous_logo_url)
        return instance


class ChatbotListSerializer(ChatbotBaseSerializer):
    """Serialize chatbots returned by the list endpoint."""


class ChatbotCreateSerializer(ChatbotBaseSerializer):
    """Validate and serialize chatbot creation."""


class ChatbotDetailSerializer(ChatbotBaseSerializer):
    """Serialize the complete details of a chatbot."""



class ChatbotWidgetSettingsSerializer(serializers.ModelSerializer):
    """Serialize the configuration used to render a chatbot widget."""

    class Meta:
        model = ChatbotWidgetSettings
        fields = (
            "id",
            "is_enabled",
            "public_key",
            "primary_color",
            "secondary_color",
            "launcher_position",
            "launcher_text",
            "header_title",
            "header_description",
            "show_branding",
            "theme",
            "other_settings",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ChatbotAllowedURLSerializer(serializers.ModelSerializer):
    """Serialize an allowed widget URL and its enabled state."""

    url = serializers.CharField(source="origin", read_only=True)

    class Meta:
        model = ChatbotAllowedOrigin
        fields = (
            "id",
            "url",
            "is_active",
        )
        read_only_fields = fields


class ChatbotWidgetDetailSerializer(serializers.ModelSerializer):
    """Serialize widget settings alongside basic chatbot details."""

    widget_settings = ChatbotWidgetSettingsSerializer(read_only=True)
    allowed_urls = ChatbotAllowedURLSerializer(
        source="allowed_origins",
        many=True,
        read_only=True,
    )

    class Meta:
        model = Chatbot
        fields = (
            "id",
            "chatbot_name",
            "business_name",
            "description",
            "slug",
            "logo",
            "welcome_message",
            "status",
            "widget_settings",
            "allowed_urls",
        )
        read_only_fields = fields


class ChatbotWidgetSettingsUpdateSerializer(serializers.ModelSerializer):
    """Validate mutable widget settings."""

    class Meta:
        model = ChatbotWidgetSettings
        fields = (
            "is_enabled",
            "primary_color",
            "secondary_color",
            "launcher_position",
            "launcher_text",
            "header_title",
            "header_description",
            "show_branding",
            "theme",
            "other_settings",
        )


class ChatbotAllowedURLUpdateSerializer(serializers.Serializer):
    """Validate a widget URL upsert or state change."""

    id = serializers.UUIDField(required=False)
    url = serializers.CharField(required=False, max_length=300)
    is_active = serializers.BooleanField(required=False)

    def validate_url(self, value):
        try:
            return normalize_widget_origin(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages[0]) from exc

    def validate(self, attrs):
        if "id" not in attrs and "url" not in attrs:
            raise serializers.ValidationError(
                "Either id or url is required."
            )
        if set(attrs) == {"id"}:
            raise serializers.ValidationError(
                "Provide url or is_active to update the allowed URL."
            )
        return attrs


class ChatbotWidgetUpdateSerializer(serializers.Serializer):
    """Update widget settings and upsert allowed URLs atomically."""

    widget_settings = ChatbotWidgetSettingsUpdateSerializer(required=False)
    allowed_urls = ChatbotAllowedURLUpdateSerializer(
        many=True,
        required=False,
    )
    removed_allowed_url_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Provide widget_settings, allowed_urls, or "
                "removed_allowed_url_id."
            )

        allowed_urls = attrs.get("allowed_urls")
        removed_origin_id = attrs.get("removed_allowed_url_id")
        if allowed_urls is None and removed_origin_id is None:
            return attrs

        existing_origins = list(self.instance.allowed_origins.all())
        origins_by_id = {origin.id: origin for origin in existing_origins}
        origins_by_url = {
            origin.origin: origin for origin in existing_origins
        }

        if (
            removed_origin_id is not None
            and removed_origin_id not in origins_by_id
        ):
            raise serializers.ValidationError(
                {
                    "removed_allowed_url_id": (
                        "Allowed URL not found for this chatbot."
                    )
                }
            )
        if allowed_urls is None:
            return attrs

        submitted_ids = set()
        submitted_urls = set()
        item_errors = {}

        for index, item in enumerate(allowed_urls):
            origin_id = item.get("id")
            url = item.get("url")
            origin = None

            if origin_id is not None:
                origin = origins_by_id.get(origin_id)
                if origin is None:
                    item_errors[index] = {
                        "id": "Allowed URL not found for this chatbot."
                    }
                    continue
            elif url is not None:
                origin = origins_by_url.get(url)

            target_url = url or origin.origin
            conflicting_origin = origins_by_url.get(target_url)
            if (
                conflicting_origin is not None
                and origin is not None
                and conflicting_origin.id != origin.id
            ):
                item_errors[index] = {
                    "url": "This URL is already configured for the chatbot."
                }
                continue

            if origin is not None and origin.id in submitted_ids:
                item_errors[index] = {
                    "id": "Each allowed URL can only be submitted once."
                }
                continue
            if target_url in submitted_urls:
                item_errors[index] = {
                    "url": "Each URL can only be submitted once."
                }
                continue

            if origin is not None:
                submitted_ids.add(origin.id)
                item["_origin_id"] = origin.id
            submitted_urls.add(target_url)

        if item_errors:
            raise serializers.ValidationError(
                {"allowed_urls": item_errors}
            )
        if removed_origin_id in submitted_ids:
            raise serializers.ValidationError(
                {
                    "removed_allowed_url_id": (
                        "A URL cannot be updated and removed together."
                    )
                }
            )
        return attrs

    def update(self, instance, validated_data):
        widget_data = validated_data.get("widget_settings")
        allowed_urls = validated_data.get("allowed_urls")
        removed_origin_id = validated_data.get("removed_allowed_url_id")
        user = self.context["request"].user

        with transaction.atomic():
            if widget_data:
                widget_settings = (
                    ChatbotWidgetSettings.objects.select_for_update().get(
                        chatbot=instance,
                    )
                )
                for field, value in widget_data.items():
                    setattr(widget_settings, field, value)
                widget_settings.updated_by = user
                widget_settings.save(
                    update_fields=[
                        *widget_data.keys(),
                        "updated_by",
                        "updated_at",
                    ]
                )

            if allowed_urls is not None:
                for item in allowed_urls:
                    origin_id = item.pop("_origin_id", None)
                    url = item.get("url")
                    is_active = item.get("is_active")

                    if origin_id is None:
                        ChatbotAllowedOrigin.objects.create(
                            chatbot=instance,
                            origin=url,
                            is_active=(
                                True if is_active is None else is_active
                            ),
                            created_by=user,
                            updated_by=user,
                        )
                        continue

                    origin = (
                        ChatbotAllowedOrigin.objects.select_for_update().get(
                            chatbot=instance,
                            id=origin_id,
                        )
                    )
                    update_fields = []
                    if url is not None and url != origin.origin:
                        origin.origin = url
                        update_fields.append("origin")
                    if (
                        is_active is not None
                        and is_active != origin.is_active
                    ):
                        origin.is_active = is_active
                        update_fields.append("is_active")
                    if update_fields:
                        origin.updated_by = user
                        origin.save(
                            update_fields=[
                                *update_fields,
                                "updated_by",
                                "updated_at",
                            ]
                        )

            if removed_origin_id is not None:
                (
                    ChatbotAllowedOrigin.objects.select_for_update()
                    .filter(
                        chatbot=instance,
                        id=removed_origin_id,
                    )
                    .delete()
                )

        return instance

    def create(self, validated_data):
        raise NotImplementedError


class ChatbotUpdateSerializer(ChatbotBaseSerializer):
    """Validate and serialize chatbot updates."""


class ChatbotDeleteSerializer(serializers.Serializer):
    """Represent the body-less chatbot delete operation."""


class ChatbotSerializer(ChatbotDetailSerializer):
    """Backward-compatible alias for the original public serializer."""


class ChatbotMemberUserSerializer(serializers.ModelSerializer):
    avatar = serializers.URLField(
        source="profile.avatar_url",
        read_only=True,
        default="",
    )

    class Meta:
        model = User
        fields = ("email", "name", "avatar")
        read_only_fields = fields


class ChatbotMemberListSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    user = ChatbotMemberUserSerializer(read_only=True)
    role = serializers.CharField(read_only=True)
    permissions = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    all_permissions = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    last_active = serializers.DateTimeField(
        source="user.last_active",
        read_only=True,
    )
    last_login = serializers.DateTimeField(
        source="user.last_login",
        read_only=True,
    )
    invited_at = serializers.DateTimeField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class ChatbotMemberSerializer(ChatbotMemberListSerializer):
    effective_permissions = serializers.SerializerMethodField()

    def get_effective_permissions(self, obj):
        return obj.effective_permissions()


class ChatbotMemberPermissionUpdateSerializer(serializers.ModelSerializer):
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=ChatbotPermissionTypes.choices),
        allow_empty=True,
    )

    class Meta:
        model = ChatbotUser
        fields = ("permissions",)

    def validate_permissions(self, value):
        try:
            return normalize_chatbot_permission_codes(
                self.instance.chatbot,
                value,
            )
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class ChatbotInvitationSerializer(serializers.ModelSerializer):
    chatbot = serializers.UUIDField(source="chatbot_id", read_only=True)
    chatbot_name = serializers.CharField(
        source="chatbot.chatbot_name",
        read_only=True,
    )

    class Meta:
        model = ChatbotInvitation
        fields = (
            "id",
            "chatbot",
            "chatbot_name",
            "email",
            "permissions",
            "invited_at",
            "expires_at",
            "created_at",
        )
        read_only_fields = fields


class InviteChatbotMemberSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=ChatbotPermissionTypes.choices),
        allow_empty=True,
        required=False,
        default=default_chatbot_user_permissions,
    )

    def validate_email(self, value):
        return User.objects.normalize_email(value).strip().casefold()

    def validate_permissions(self, value):
        try:
            return normalize_chatbot_permission_codes(
                self.context["chatbot"],
                value,
            )
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def create(self, validated_data):
        try:
            return issue_chatbot_invitation(
                chatbot=self.context["chatbot"],
                email=validated_data["email"],
                permissions=validated_data["permissions"],
                invited_by=self.context["request"].user,
            )
        except InvalidChatbotInvitation as exc:
            raise serializers.ValidationError({"email": str(exc)}) from exc


class AcceptChatbotInvitationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)
    token = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )

        try:
            invitation = get_valid_chatbot_invitation(attrs["token"])
        except InvalidChatbotInvitation as exc:
            raise serializers.ValidationError({"token": str(exc)}) from exc

        user = User.objects.filter(
            email__iexact=invitation.email,
            is_active=True,
        ).first()
        if user is None:
            raise serializers.ValidationError(
                {"token": "The invited user account is no longer active."}
            )
        user.name = attrs["name"]
        validate_password(attrs["password"], user)
        return attrs

    def create(self, validated_data):
        try:
            return accept_chatbot_invitation(
                token=validated_data["token"],
                name=validated_data["name"],
                password=validated_data["password"],
            )
        except InvalidChatbotInvitation as exc:
            raise serializers.ValidationError({"token": str(exc)}) from exc
