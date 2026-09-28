from rest_framework import serializers

from chat.models import ChatMessage, ChatMessageAttachment, ChatSession
from chat.services.visitor_tokens import issue_conversation_token
from chat.utils.choices import ChatMessageSenderType


class PublicChatbotAgentSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(source="user.name", read_only=True)
    avatar_url = serializers.URLField(
        source="user.profile.avatar_url",
        read_only=True,
        default="",
    )


class PublicChatMessageAttachmentSerializer(serializers.ModelSerializer):
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
    sender = PublicChatbotAgentSerializer(read_only=True)
    attachments = PublicChatMessageAttachmentSerializer(
        many=True,
        read_only=True,
    )

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


class PublicVisitorSessionSerializer(serializers.ModelSerializer):
    conversation_token = serializers.SerializerMethodField()
    message_count = serializers.IntegerField(read_only=True)
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = (
            "id",
            "status",
            "created_at",
            "last_activity_at",
            "conversation_token",
            "message_count",
            "last_message",
        )
        read_only_fields = fields

    def get_conversation_token(self, obj):
        return issue_conversation_token(obj)

    def get_last_message(self, obj):
        sender_type = getattr(obj, "last_message_sender_type", None)
        if sender_type is None:
            return None

        public_sender_types = {
            ChatMessageSenderType.VISITOR: "You",
            ChatMessageSenderType.AI: obj.chatbot.chatbot_name,
            ChatMessageSenderType.AGENT: (
                obj.last_message_agent_name or "Support Agent"
            ),
            ChatMessageSenderType.SYSTEM: obj.chatbot.chatbot_name,
        }
        return {
            "content": obj.last_message_content,
            "sender": public_sender_types[sender_type],
            "created_at": serializers.DateTimeField().to_representation(
                obj.last_message_created_at
            ),
        }


class VisitorSessionCreateSerializer(serializers.Serializer):
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


class VisitorQuerySerializer(serializers.Serializer):
    visitor_id = serializers.CharField(max_length=255)


class VisitorSessionQuerySerializer(VisitorQuerySerializer):
    session_id = serializers.UUIDField()


class VisitorMessageListQuerySerializer(VisitorSessionQuerySerializer):
    conversation_token = serializers.CharField(max_length=2048)


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


