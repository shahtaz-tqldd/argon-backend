from django.contrib.auth import get_user_model
from rest_framework import serializers

from chatbot.api.v1.client.serializers import ChatbotUpdateSerializer
from chatbot.models import Chatbot
from workspace.models import Workspace

User = get_user_model()


class AdminChatbotListQuerySerializer(serializers.Serializer):
    workspace = serializers.SlugField()


class AdminChatbotQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()


class AdminChatbotWorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ("id", "name", "slug")
        read_only_fields = fields


class AdminChatbotUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "name", "email")
        read_only_fields = fields


class AdminChatbotListSerializer(serializers.ModelSerializer):
    workspace = AdminChatbotWorkspaceSerializer(read_only=True)
    created_by = AdminChatbotUserSerializer(read_only=True)

    class Meta:
        model = Chatbot
        fields = (
            "id",
            "workspace",
            "chatbot_name",
            "business_name",
            "description",
            "slug",
            "logo",
            "ai_enabled",
            "status",
            "created_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AdminChatbotDetailSerializer(serializers.ModelSerializer):
    workspace = AdminChatbotWorkspaceSerializer(read_only=True)
    created_by = AdminChatbotUserSerializer(read_only=True)
    updated_by = AdminChatbotUserSerializer(read_only=True)

    class Meta:
        model = Chatbot
        fields = (
            "id",
            "workspace",
            "chatbot_name",
            "business_name",
            "description",
            "slug",
            "logo",
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
            "status",
            "is_deleted",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AdminChatbotUpdateSerializer(ChatbotUpdateSerializer):
    """Apply the same validated chatbot updates without membership checks."""
