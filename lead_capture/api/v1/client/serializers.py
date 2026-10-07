from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from chat.models import ChatMessage
from chatbot.models import ChatbotVisitor
from lead_capture.models import Lead, LeadAIInsight, LeadCaptureConfig, LeadNote, LeadSignal


class LeadChatbotQuerySerializer(serializers.Serializer):
    chatbot_slug = serializers.SlugField()


class LeadSignalQuerySerializer(LeadChatbotQuerySerializer):
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


class LeadGrowthQuerySerializer(LeadSignalQuerySerializer):
    interval = serializers.ChoiceField(
        choices=("day", "week", "month"),
        default="day",
    )


class LeadExportQuerySerializer(LeadChatbotQuerySerializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    file_format = serializers.ChoiceField(
        choices=("csv", "xlsx", "excel"),
    )

    def validate(self, attrs):
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError(
                {"end_date": "end_date must be on or after start_date."}
            )
        if attrs["file_format"] == "excel":
            attrs["file_format"] = "xlsx"
        return attrs


class LeadQuerySerializer(LeadChatbotQuerySerializer):
    lead_id = serializers.UUIDField()


class LeadNoteQuerySerializer(LeadQuerySerializer):
    note_id = serializers.UUIDField()


class LeadCaptureConfigSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = LeadCaptureConfig
        fields = (
            "id",
            "chatbot_id",
            "is_enabled",
            "collectable_fields",
            "auto_collect",
            "require_consent",
            "consent_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "chatbot_id", "created_at", "updated_at")

    def create(self, validated_data):
        request = self.context["request"]
        config = LeadCaptureConfig(
            chatbot=self.context["chatbot"],
            created_by=request.user,
            updated_by=request.user,
            **validated_data,
        )
        try:
            config.full_clean()
            config.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", exc.messages)
            ) from exc
        return config

    def update(self, instance, validated_data):
        for field_name, field_value in validated_data.items():
            setattr(instance, field_name, field_value)
        instance.updated_by = self.context["request"].user
        try:
            instance.full_clean()
            instance.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", exc.messages)
            ) from exc
        return instance


class LeadSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)
    notes_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Lead
        fields = (
            "id",
            "chatbot_id",
            "collected_fields",
            "status",
            "avg_score",
            "source",
            "notes_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class LeadVisitorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatbotVisitor
        fields = (
            "ip_address",
            "detected_location",
            "detected_country",
            "metadata"
        )
        read_only_fields = fields


class LeadDetailSerializer(LeadSerializer):
    visitor = serializers.SerializerMethodField()

    class Meta(LeadSerializer.Meta):
        fields = LeadSerializer.Meta.fields + ("visitor",)

    def get_visitor(self, instance):
        # Reverse one-to-one: leads may exist without a visitor (e.g. leads
        # created outside the widget), and accessing it raises in that case.
        visitor = getattr(instance, "visitor", None)
        if visitor is None:
            return None
        return LeadVisitorSerializer(visitor, context=self.context).data


class LeadUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = (
            "collected_fields",
            "status",
            "avg_score",
            "source",
        )

    def validate_avg_score(self, value):
        if value is not None and not 0 <= value <= 100:
            raise serializers.ValidationError(
                "Average lead score must be between 0 and 100."
            )
        return value

    def update(self, instance, validated_data):
        for field_name, field_value in validated_data.items():
            setattr(instance, field_name, field_value)
        try:
            instance.full_clean()
            instance.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", exc.messages)
            ) from exc
        return instance


class LeadAIInsightSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = LeadAIInsight
        fields = (
            "id",
            "chatbot_id",
            "week_start",
            "week_end",
            "session_count",
            "visitor_message_count",
            "summary",
            "topics",
            "frequently_asked_questions",
            "common_intents",
            "areas_of_improvement",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class LeadSignalSerializer(serializers.ModelSerializer):
    lead_id = serializers.UUIDField(read_only=True)
    message_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = LeadSignal
        fields = (
            "id",
            "lead_id",
            "message_id",
            "score",
            "summary",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class LeadSignalMessageSerializer(serializers.ModelSerializer):
    chat_session_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = ChatMessage
        fields = (
            "id",
            "chat_session_id",
            "sender_type",
            "content",
            "created_at",
        )
        read_only_fields = fields


class LeadSignalDetailSerializer(serializers.ModelSerializer):
    lead_id = serializers.UUIDField(read_only=True)
    message = serializers.SerializerMethodField()

    class Meta:
        model = LeadSignal
        fields = (
            "id",
            "lead_id",
            "score",
            "summary",
            "message",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_message(self, instance):
        if instance.message is None:
            return None
        return LeadSignalMessageSerializer(instance.message).data


class LeadNoteSerializer(serializers.ModelSerializer):
    lead_id = serializers.UUIDField(read_only=True)
    author = serializers.SerializerMethodField()

    class Meta:
        model = LeadNote
        fields = (
            "id",
            "lead_id",
            "author",
            "content",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "lead_id", "author", "created_at", "updated_at")

    def get_author(self, instance):
        user = instance.author.user
        return {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
        }

    def create(self, validated_data):
        note = LeadNote(
            lead=self.context["lead"],
            author=self.context["chatbot_user"],
            **validated_data,
        )
        try:
            note.full_clean()
            note.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", exc.messages)
            ) from exc
        return note

    def update(self, instance, validated_data):
        for field_name, field_value in validated_data.items():
            setattr(instance, field_name, field_value)
        try:
            instance.full_clean()
            instance.save()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", exc.messages)
            ) from exc
        return instance
