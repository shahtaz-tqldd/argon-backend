from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from workspace.models import Workspace, WorkspaceInvitation
from workspace.services.invitations import (
    InvalidWorkspaceInvitation,
    accept_workspace_invitation,
    get_valid_workspace_invitation,
    issue_workspace_invitation,
)

User = get_user_model()


class WorkspaceOwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "name")
        read_only_fields = fields


class WorkspaceSerializer(serializers.ModelSerializer):
    owner = WorkspaceOwnerSerializer(read_only=True)
    member_count = serializers.SerializerMethodField()

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
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "slug",
            "owner",
            "member_count",
            "is_active",
            "created_at",
            "updated_at",
        )

    def get_member_count(self, obj):
        return obj.memberships.filter(is_active=True).count()

    def update(self, instance, validated_data):
        instance.updated_by = self.context["request"].user
        return super().update(instance, validated_data)


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
