from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from workspace.models import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceRole,
    WorkspaceUser,
)
from workspace.services.invitations import (
    InvalidWorkspaceInvitation,
    accept_workspace_invitation,
    get_valid_workspace_invitation,
    issue_workspace_invitation,
)

User = get_user_model()


class WorkspaceQuerySerializer(serializers.Serializer):
    workspace = serializers.SlugField()


class WorkspaceMemberQuerySerializer(WorkspaceQuerySerializer):
    member_email = serializers.EmailField(max_length=254)

    def validate_member_email(self, value):
        return User.objects.normalize_email(value).strip().casefold()


class WorkspaceOwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "name")
        read_only_fields = fields


class WorkspaceBaseSerializer(serializers.ModelSerializer):
    owner = WorkspaceOwnerSerializer(read_only=True)
    member_count = serializers.SerializerMethodField()
    current_user_role = serializers.SerializerMethodField()

    class Meta:
        model = Workspace
        fields = (
            "id",
            "name",
            "slug",
            "logo",
            "industry",
            "owner",
            "member_count",
            "current_user_role",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "slug",
            "owner",
            "member_count",
            "current_user_role",
            "is_active",
            "created_at",
            "updated_at",
        )

    def get_member_count(self, obj):
        return obj.memberships.filter(is_active=True).count()

    def get_current_user_role(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        return (
            obj.memberships.filter(
                user=request.user,
                is_active=True,
            )
            .values_list("role", flat=True)
            .first()
        )

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("This field is required.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        workspace = Workspace.objects.create(
            owner=user,
            created_by=user,
            **validated_data,
        )
        WorkspaceUser.objects.create(
            workspace=workspace,
            user=user,
            role=WorkspaceRole.ADMIN,
            created_by=user,
        )
        return workspace

    def update(self, instance, validated_data):
        instance.updated_by = self.context["request"].user
        return super().update(instance, validated_data)


class WorkspaceListSerializer(WorkspaceBaseSerializer):
    """Serialize workspaces returned by the list endpoint."""


class WorkspaceCreateSerializer(WorkspaceBaseSerializer):
    """Validate and serialize workspace creation."""


class WorkspaceDetailSerializer(WorkspaceBaseSerializer):
    """Serialize complete workspace details."""


class WorkspaceUpdateSerializer(WorkspaceBaseSerializer):
    """Validate and serialize workspace updates."""


class WorkspaceDeleteSerializer(serializers.Serializer):
    """Represent the body-less workspace delete operation."""


class WorkspaceSerializer(WorkspaceDetailSerializer):
    """Backward-compatible alias for the original public serializer."""


class WorkspaceMemberUserSerializer(serializers.ModelSerializer):
    avatar = serializers.URLField(
        source="profile.avatar_url",
        read_only=True,
        default="",
    )

    class Meta:
        model = User
        fields = ("email", "name", "avatar")
        read_only_fields = fields


class WorkspaceMemberListSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    user = WorkspaceMemberUserSerializer(read_only=True)
    role = serializers.CharField(read_only=True)
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


class WorkspaceMemberSerializer(WorkspaceMemberListSerializer):
    """Serialize an active workspace membership."""


class WorkspaceMemberRoleUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkspaceUser
        fields = ("role",)

    def validate_role(self, value):
        if (
            self.instance.workspace.owner_id == self.instance.user_id
            and value != WorkspaceRole.ADMIN
        ):
            raise serializers.ValidationError(
                "The workspace owner must remain an admin."
            )
        return value


class WorkspaceInvitationSerializer(serializers.ModelSerializer):
    workspace = serializers.SlugField(source="workspace.slug", read_only=True)

    class Meta:
        model = WorkspaceInvitation
        fields = ("id", "workspace", "email", "expires_at", "created_at")
        read_only_fields = fields


class InviteWorkspaceMemberSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value):
        return User.objects.normalize_email(value).strip().casefold()

    def create(self, validated_data):
        try:
            return issue_workspace_invitation(
                workspace=self.context["workspace"],
                email=validated_data["email"],
                invited_by=self.context["request"].user,
            )
        except InvalidWorkspaceInvitation as exc:
            raise serializers.ValidationError({"email": str(exc)}) from exc


class AcceptWorkspaceInvitationSerializer(serializers.Serializer):
    token = serializers.CharField(write_only=True)
    name = serializers.CharField(max_length=50)
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )
        try:
            invitation = get_valid_workspace_invitation(attrs["token"])
        except InvalidWorkspaceInvitation as exc:
            raise serializers.ValidationError({"token": str(exc)}) from exc

        validate_password(
            attrs["password"],
            User(email=invitation.email, name=attrs["name"]),
        )
        attrs["invitation"] = invitation
        return attrs

    def create(self, validated_data):
        try:
            return accept_workspace_invitation(
                token=validated_data["token"],
                name=validated_data["name"],
                password=validated_data["password"],
            )
        except InvalidWorkspaceInvitation as exc:
            raise serializers.ValidationError({"token": str(exc)}) from exc
