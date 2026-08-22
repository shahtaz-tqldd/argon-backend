from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from chatbot.models import Chatbot, ChatbotInvitation, ChatbotUser
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
from chatbot.utils.validation import validate_unique_chatbot_name
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
            "name",
            "description",
            "slug",
            "welcome_message",
            "fallback_message",
            "instructions",
            "language",
            "timezone",
            "ai_enabled",
            "knowledge_base_enabled",
            "human_handoff_enabled",
            "other_settings",
            "logo",
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

        if "name" in attrs:
            name = attrs["name"].strip()
            if not name:
                raise serializers.ValidationError({"name": "This field is required."})
            try:
                validate_unique_chatbot_name(
                    workspace=workspace,
                    name=name,
                    chatbot_id=self.instance.pk if self.instance else None,
                )
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {"name": exc.messages[0]}
                ) from exc
            attrs["name"] = name
        return attrs

    def create(self, validated_data):
        workspace = validated_data.pop("workspace")
        try:
            return create_chatbot(
                workspace=workspace,
                created_by=self.context["request"].user,
                **validated_data,
            )
        except (DjangoValidationError, ValueError) as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            raise serializers.ValidationError({"non_field_errors": message}) from exc

    def update(self, instance, validated_data):
        instance.updated_by = self.context["request"].user
        for field, value in validated_data.items():
            setattr(instance, field, value)
        update_fields = [*validated_data.keys(), "updated_by", "updated_at"]
        instance.save(update_fields=update_fields)
        return instance


class ChatbotListSerializer(ChatbotBaseSerializer):
    """Serialize chatbots returned by the list endpoint."""


class ChatbotCreateSerializer(ChatbotBaseSerializer):
    """Validate and serialize chatbot creation."""


class ChatbotDetailSerializer(ChatbotBaseSerializer):
    """Serialize the complete details of a chatbot."""


class ChatbotShortDetailSerializer(ChatbotBaseSerializer):
    """Serialize the short-detail endpoint.

    This currently preserves the existing response contract. Its dedicated class
    allows the short representation to evolve without affecting other endpoints.
    """


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
    chatbot_name = serializers.CharField(source="chatbot.name", read_only=True)

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
