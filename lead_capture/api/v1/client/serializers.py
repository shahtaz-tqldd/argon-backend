from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from lead_capture.models import Lead, LeadCaptureConfig, LeadNote


class LeadChatbotQuerySerializer(serializers.Serializer):
    chatbot_slug = serializers.SlugField()


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
            "intro_message",
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
            "initial_ip_address",
            "last_ip_address",
            "detected_country_code",
            "detected_city",
            "status",
            "lead_score",
            "source",
            "notes_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class LeadUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = (
            "collected_fields",
            "status",
            "lead_score",
            "source",
        )

    def validate_lead_score(self, value):
        if value is not None and value > 100:
            raise serializers.ValidationError(
                "Lead score must be between 0 and 100."
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
