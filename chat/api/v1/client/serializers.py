from rest_framework import serializers

from chat.api.v1.attachments import (
    message_attachment_field,
    validate_message_payload,
)
from chat.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
    ChatSessionTakeover,
    ChatSessionTransfer,
)
from chat.utils.choices import (
    ChatMessageSenderType,
    ChatSessionStatus,
    ChatSessionTakeoverReleaseReason,
    ChatSessionTransferStatus,
)


class ChatSessionQuerySerializer(serializers.Serializer):
    chatbot_slug = serializers.SlugField()


class ChatSessionObjectQuerySerializer(ChatSessionQuerySerializer):
    session_id = serializers.UUIDField()


class ChatSessionTranscriptQuerySerializer(ChatSessionObjectQuerySerializer):
    format = serializers.ChoiceField(
        choices=("csv", "pdf"),
        required=False,
    )
    file_format = serializers.ChoiceField(
        choices=("csv", "pdf"),
        required=False,
    )

    def validate(self, attrs):
        requested_format = attrs.get("format")
        file_format = attrs.get("file_format")
        if requested_format and file_format and requested_format != file_format:
            raise serializers.ValidationError(
                {"format": "format and file_format must match when both are provided."}
            )
        resolved_format = requested_format or file_format
        if resolved_format is None:
            raise serializers.ValidationError(
                {"format": "This field is required."}
            )
        attrs["file_format"] = resolved_format
        return attrs


class ChatSessionListQuerySerializer(ChatSessionQuerySerializer):
    search = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=255,
    )
    assigned_to = serializers.EmailField(required=False)
    channel = serializers.ChoiceField(
        choices=("web_widget", "api", "messenger", "instagram", "whats_app"),
        required=False,
    )
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
    is_recently_active = serializers.BooleanField(required=False)
    my_session = serializers.BooleanField(required=False)

    def validate_assigned_to(self, value):
        return value.strip().casefold()


class TestChatSessionSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)
    message_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = ChatSession
        fields = (
            "id",
            "chatbot_id",
            "is_test",
            "status",
            "message_count",
            "last_activity_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class TestChatMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False, max_length=10000)

    def validate_content(self, value):
        if not value.strip():
            raise serializers.ValidationError("Message content cannot be blank.")
        return value


class TestChatCapacitySerializer(serializers.Serializer):
    test_ai_message_limit = serializers.IntegerField(read_only=True)
    current_test_ai_message_count = serializers.IntegerField(read_only=True)
    test_ai_messages_remaining = serializers.SerializerMethodField()
    ai_message_limit = serializers.IntegerField(read_only=True, allow_null=True)
    current_ai_message_count = serializers.IntegerField(read_only=True)
    subscription_ai_messages_remaining = serializers.SerializerMethodField()

    def get_test_ai_messages_remaining(self, obj):
        return max(
            obj.test_ai_message_limit - obj.current_test_ai_message_count,
            0,
        )

    def get_subscription_ai_messages_remaining(self, obj):
        if obj.ai_message_limit is None:
            return None
        return max(obj.ai_message_limit - obj.current_ai_message_count, 0)


class SessionOverviewQuerySerializer(ChatSessionQuerySerializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs):
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError(
                {"end_date": "end_date must be on or after start_date."}
            )
        return attrs


class ChatSessionTransferObjectQuerySerializer(ChatSessionQuerySerializer):
    transfer_id = serializers.UUIDField()


class ChatSessionTransferListQuerySerializer(ChatSessionQuerySerializer):
    status = serializers.ChoiceField(
        choices=ChatSessionTransferStatus.choices,
        required=False,
    )


class ChatbotAgentSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(source="user.name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    avatar_url = serializers.URLField(
        source="user.profile.avatar_url",
        read_only=True,
        default="",
    )


class ChatSessionAgentSerializer(serializers.Serializer):
    """Minimal agent representation used by the session inbox."""

    name = serializers.CharField(source="user.name", read_only=True)


class ChatSessionChatbotSerializer(serializers.Serializer):
    slug = serializers.SlugField(read_only=True)
    chatbot_name = serializers.CharField(read_only=True)
    logo = serializers.URLField(read_only=True)


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
    chatbot = ChatSessionChatbotSerializer(read_only=True)
    lead_id = serializers.UUIDField(read_only=True)
    assigned_to = ChatbotAgentSerializer(read_only=True)
    user_metadata = serializers.SerializerMethodField()
    message_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = ChatSession
        fields = (
            "id",
            "chatbot",
            "channel",
            "status",
            "lead_id",
            "assigned_to",
            "visitor_id",
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

    def get_user_metadata(self, obj):
        user_metadata = (
            dict(obj.user_metadata)
            if isinstance(obj.user_metadata, dict)
            else {}
        )
        lead_fields = (
            obj.lead.collected_fields
            if obj.lead and isinstance(obj.lead.collected_fields, dict)
            else {}
        )
        for field_name in ("name", "email", "phone"):
            lead_value = lead_fields.get(field_name)
            if lead_value not in (None, ""):
                user_metadata[field_name] = lead_value
        return user_metadata


class ChatSessionListSerializer(serializers.ModelSerializer):
    user_data = serializers.SerializerMethodField()
    unread_message_count = serializers.IntegerField(read_only=True)
    last_message = serializers.SerializerMethodField()
    assigned_to = ChatSessionAgentSerializer(read_only=True)
    transfer_requested_to = serializers.SerializerMethodField()
    is_blocked = serializers.BooleanField(read_only=True)

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
            "transfer_requested_to",
            "is_blocked",
            "requires_attention",
            "attention_reason",
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
        return {
            "name": self._first_value(
                lead_fields.get("name"),
                user_metadata.get("name"),
            ),
            "detected_country": self._first_value(
                lead.detected_country_code if lead else "",
                user_metadata.get("detected_country"),
                user_metadata.get("detected_country_code"),
            ),
        }

    def get_transfer_requested_to(self, obj):
        name = obj.transfer_requested_to_name
        return {"name": name} if name is not None else None

    def get_last_message(self, obj):
        if obj.last_message_sender is None:
            return None

        if obj.last_message_sender == ChatMessageSenderType.AI:
            sender = obj.chatbot.chatbot_name
        elif obj.last_message_sender == ChatMessageSenderType.AGENT:
            sender = obj.last_message_agent_name or "agent"
        elif obj.last_message_sender == ChatMessageSenderType.VISITOR:
            sender = self.get_user_data(obj).get("name") or "Visitor"
        elif obj.last_message_sender == ChatMessageSenderType.SYSTEM:
            sender = "System"
        else:
            sender = obj.last_message_sender

        return {
            "sender": sender,
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
            "is_forced",
            "takeover_reason",
            "released_at",
            "release_reason",
            "released_to",
            "resolution_note",
            "reopened_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class TakeOverSessionSerializer(serializers.Serializer):
    is_forced = serializers.BooleanField(required=False, default=False)
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=256,
    )

    def validate(self, attrs):
        attrs["reason"] = attrs["reason"].strip()
        if attrs["is_forced"] and not attrs["reason"]:
            raise serializers.ValidationError(
                {"reason": "A reason is required for a forced takeover."}
            )
        return attrs


class ForceReturnToAISerializer(serializers.Serializer):
    note = serializers.CharField(max_length=5000, trim_whitespace=True)

    def validate_note(self, value):
        if not value:
            raise serializers.ValidationError(
                "A note is required for a forced return to AI."
            )
        return value


class AgentMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(
        trim_whitespace=False,
        max_length=10000,
        required=False,
        allow_blank=True,
        default="",
    )
    attachments = message_attachment_field()
    metadata = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        return validate_message_payload(attrs)

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Metadata must be a JSON object.")
        return value



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
