from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from lead_capture.models import Lead, LeadCaptureConfig, LeadNote
from lead_capture.utils.choices import LeadCaptureFieldMode
from lead_capture.utils.validators import MAX_CAPTURE_FIELDS


class LeadChatbotQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()


class LeadQuerySerializer(LeadChatbotQuerySerializer):
    lead_id = serializers.UUIDField()


class LeadCaptureConfigSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = LeadCaptureConfig
        fields = (
            "id",
            "chatbot_id",
            "is_enabled",
            "name_mode",
            "email_mode",
            "phone_mode",
            "address_mode",
            "custom_fields",
            "auto_collect",
            "intro_message",
            "require_consent",
            "consent_message",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "chatbot_id", "created_at", "updated_at")

    def validate(self, attrs):
        attrs = super().validate(attrs)

        def value(field_name):
            if field_name in attrs:
                return attrs[field_name]
            if self.instance is not None:
                return getattr(self.instance, field_name)
            return LeadCaptureConfig._meta.get_field(field_name).get_default()

        standard_count = sum(
            value(field_name) != LeadCaptureFieldMode.HIDDEN
            for field_name in (
                "name_mode",
                "email_mode",
                "phone_mode",
                "address_mode",
            )
        )
        custom_fields = value("custom_fields")
        custom_count = len(custom_fields) if isinstance(custom_fields, list) else 0
        total_count = standard_count + custom_count

        if value("is_enabled") and total_count == 0:
            raise serializers.ValidationError(
                "At least one lead field must be optional or required when "
                "lead capture is enabled."
            )
        if total_count > MAX_CAPTURE_FIELDS:
            raise serializers.ValidationError(
                {
                    "custom_fields": (
                        f"No more than {MAX_CAPTURE_FIELDS} required and "
                        "optional fields can be configured in total."
                    )
                }
            )
        return attrs

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
            "name",
            "email",
            "phone",
            "address",
            "custom_fields",
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
            "name",
            "email",
            "phone",
            "address",
            "custom_fields",
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
