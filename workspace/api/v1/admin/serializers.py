from django.contrib.auth import get_user_model
from rest_framework import serializers

from workspace.api.v1.client.serializers import WorkspaceUpdateSerializer
from workspace.models import Workspace

User = get_user_model()


class AdminWorkspaceQuerySerializer(serializers.Serializer):
    workspace = serializers.SlugField()


class AdminWorkspaceUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "name", "email")
        read_only_fields = fields


class AdminWorkspaceListSerializer(serializers.ModelSerializer):
    owner = AdminWorkspaceUserSerializer(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    chatbot_count = serializers.IntegerField(read_only=True)

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
            "chatbot_count",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AdminWorkspaceDetailSerializer(serializers.ModelSerializer):
    owner = AdminWorkspaceUserSerializer(read_only=True)
    created_by = AdminWorkspaceUserSerializer(read_only=True)
    updated_by = AdminWorkspaceUserSerializer(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    chatbot_count = serializers.IntegerField(read_only=True)

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
            "chatbot_count",
            "is_active",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AdminWorkspaceUpdateSerializer(WorkspaceUpdateSerializer):
    """Apply existing workspace validation and logo update behavior."""
