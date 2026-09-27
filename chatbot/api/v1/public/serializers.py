from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from appointment_booking.models import AppointmentBookingConfig
from chatbot.models import Chatbot, ChatbotWidgetSettings
from lead_capture.models import LeadCaptureConfig


class PublicChatbotWidgetSettingsSerializer(serializers.ModelSerializer):
    """Serialize only the widget configuration safe for public clients."""

    class Meta:
        model = ChatbotWidgetSettings
        fields = (
            "primary_color",
            "secondary_color",
            "launcher_position",
            "launcher_text",
            "header_title",
            "header_description",
            "show_branding",
            "theme",
            "other_settings",
        )
        read_only_fields = fields


class PublicLeadCaptureConfigSerializer(serializers.ModelSerializer):
    """Serialize lead capture settings required by the public widget."""

    class Meta:
        model = LeadCaptureConfig
        fields = (
            "is_enabled",
            "collectable_fields",
            "auto_collect",
            "require_consent",
            "consent_message",
        )
        read_only_fields = fields


class PublicAppointmentBookingConfigSerializer(serializers.ModelSerializer):
    """Serialize the booking form configuration required by the widget."""

    class Meta:
        model = AppointmentBookingConfig
        fields = (
            "collectable_fields",
            "confirmation_message",
        )
        read_only_fields = fields


class PublicVisitorSerializer(serializers.Serializer):
    visitor_id = serializers.CharField(read_only=True)
    lead_id = serializers.UUIDField(read_only=True, allow_null=True)
    lead_data = serializers.JSONField(read_only=True)
    user_metadata = serializers.JSONField(read_only=True)


class PublicChatbotSerializer(serializers.ModelSerializer):
    """Serialize the public configuration consumed by an embedded widget."""

    widget_settings = PublicChatbotWidgetSettingsSerializer(read_only=True)
    lead_config = serializers.SerializerMethodField()
    appointment_config = serializers.SerializerMethodField()

    def get_lead_config(self, obj):
        try:
            config = obj.lead_capture_config
        except ObjectDoesNotExist:
            return None

        if not config.is_enabled:
            return None
        return PublicLeadCaptureConfigSerializer(config).data

    def get_appointment_config(self, obj):
        try:
            config = obj.appointment_booking_config
        except ObjectDoesNotExist:
            return None

        if not config.is_enabled:
            return None
        return PublicAppointmentBookingConfigSerializer(config).data

    class Meta:
        model = Chatbot
        fields = (
            "chatbot_name",
            "logo",
            "language",
            "welcome_message",
            "widget_settings",
            "lead_config",
            "appointment_config",
        )
        read_only_fields = fields
