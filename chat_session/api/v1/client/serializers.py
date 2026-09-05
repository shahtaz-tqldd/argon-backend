from rest_framework import serializers

from chat_session.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
    ChatSessionTakeover,
    ChatSessionTransfer,
)
from chat_session.utils.choices import (
    ChatSessionStatus,
    ChatSessionTakeoverReleaseReason,
    ChatSessionTransferStatus,
)


class ChatSessionQuerySerializer(serializers.Serializer):
    chatbot_slug = serializers.SlugField()


class ChatSessionObjectQuerySerializer(ChatSessionQuerySerializer):
    session_id = serializers.UUIDField()


class ChatSessionListQuerySerializer(ChatSessionQuerySerializer):
    status = serializers.ChoiceField(
        choices=ChatSessionStatus.choices,
        required=False,
    )
    assignment = serializers.ChoiceField(
        choices=("all", "mine", "assigned", "unassigned"),
        default="all",
        required=False,
    )
    requires_attention = serializers.BooleanField(required=False)


class ChatSessionTransferObjectQuerySerializer(ChatSessionQuerySerializer):
    transfer_id = serializers.UUIDField()


class ChatSessionTransferListQuerySerializer(ChatSessionQuerySerializer):
    status = serializers.ChoiceField(
        choices=ChatSessionTransferStatus.choices,
        required=False,
    )


class ChatbotAgentSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    name = serializers.CharField(source="user.name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)


class ChatMessageAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessageAttachment
        fields = (
            "id",
            "attachment_type",
            "file_url",
            "file_name",
            "mime_type",
            "file_size",
            "duration_ms",
            "sort_order",
            "created_at",
        )
        read_only_fields = fields


class ChatMessageSerializer(serializers.ModelSerializer):
    chat_session_id = serializers.UUIDField(read_only=True)
    sender = ChatbotAgentSerializer(read_only=True)
    attachments = ChatMessageAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "chat_session_id",
            "sender_type",
            "sender",
            "content",
            "status",
            "external_id",
            "metadata",
            "attachments",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ChatSessionSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)
    lead_id = serializers.UUIDField(read_only=True)
    assigned_to = ChatbotAgentSerializer(read_only=True)
    message_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = ChatSession
        fields = (
            "id",
            "chatbot_id",
            "channel",
            "status",
            "lead_id",
            "assigned_to",
            "visitor_id",
            "external_thread_id",
            "last_activity_at",
            "last_visitor_activity_at",
            "requires_attention",
            "attention_reason",
            "attention_requested_at",
            "resolved_at",
            "closed_at",
            "is_recently_active",
            "ai_enabled",
            "user_metadata",
            "metadata",
            "message_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ChatSessionListSerializer(serializers.ModelSerializer):
    user_data = serializers.SerializerMethodField()
    unread_message_count = serializers.IntegerField(read_only=True)
    last_message = serializers.SerializerMethodField()
    assigned_to = ChatbotAgentSerializer(read_only=True)

    class Meta:
        model = ChatSession
        fields = (
            "id",
            "channel",
            "user_data",
            "unread_message_count",
            "last_message",
            "ai_enabled",
            "is_recently_active",
            "status",
            "assigned_to",
            "requires_attention",
            "last_activity_at",
        )
        read_only_fields = fields

    @staticmethod
    def _first_value(*values):
        return next((value for value in values if value not in (None, "")), "")

    def get_user_data(self, obj):
        lead = obj.lead
        lead_fields = lead.collected_fields if lead else {}
        user_metadata = obj.user_metadata or {}
        user_data = {**user_metadata, **lead_fields}
        user_data["name"] = self._first_value(
            lead_fields.get("name"),
            user_metadata.get("name"),
        )
        user_data["detected_country"] = self._first_value(
            lead.detected_country_code if lead else "",
            user_metadata.get("detected_country"),
            user_metadata.get("detected_country_code"),
        )
        user_data.pop("detected_country_code", None)
        return user_data

    def get_last_message(self, obj):
        if obj.last_message_sender is None:
            return None
        return {
            "sender": obj.last_message_sender,
            "content": obj.last_message_content,
        }


class ChatSessionTakeoverSerializer(serializers.ModelSerializer):
    chat_session_id = serializers.UUIDField(read_only=True)
    agent = ChatbotAgentSerializer(read_only=True)
    released_to = ChatbotAgentSerializer(read_only=True)

    class Meta:
        model = ChatSessionTakeover
        fields = (
            "id",
            "chat_session_id",
            "agent",
            "released_at",
            "release_reason",
            "released_to",
            "resolution_note",
            "reopened_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class AgentMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False, max_length=10000)
    metadata = serializers.JSONField(required=False, default=dict)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("Message content cannot be blank.")
        return value

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Metadata must be a JSON object.")
        return value


class VisitorConversationCreateSerializer(serializers.Serializer):
    conversation_token = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2048,
    )
    user_metadata = serializers.JSONField(required=False)
    metadata = serializers.JSONField(required=False)

    def validate_user_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be a JSON object.")
        return value

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be a JSON object.")
        return value


class VisitorMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False, max_length=10000)
    client_message_id = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
    )
    metadata = serializers.JSONField(required=False, default=dict)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("Message content cannot be blank.")
        return value

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be a JSON object.")
        return value


class VisitorMessageSerializer(ChatMessageSerializer):
    """Public message representation without internal agent contact details."""

    sender = serializers.SerializerMethodField()

    def get_sender(self, obj):
        if obj.sender_id is None:
            return None
        return {
            "name": obj.sender.user.name,
            "avatar": getattr(
                getattr(obj.sender.user, "profile", None),
                "avatar_url",
                "",
            ),
        }


class ChatSessionTransferSerializer(serializers.ModelSerializer):
    chat_session_id = serializers.UUIDField(read_only=True)
    from_agent = ChatbotAgentSerializer(read_only=True)
    to_agent = ChatbotAgentSerializer(read_only=True)

    class Meta:
        model = ChatSessionTransfer
        fields = (
            "id",
            "chat_session_id",
            "from_agent",
            "to_agent",
            "status",
            "reason",
            "completed_at",
            "expires_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class TransferSessionSerializer(serializers.Serializer):
    to_agent_id = serializers.UUIDField()
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=5000,
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class ResolveSessionSerializer(serializers.Serializer):
    resolution_type = serializers.ChoiceField(
        choices=(
            ChatSessionTakeoverReleaseReason.RESOLVED,
            ChatSessionTakeoverReleaseReason.CLOSED,
        )
    )
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=5000,
    )
