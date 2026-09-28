from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from appointment.models import Appointment


class VisitorAppointmentQuerySerializer(serializers.Serializer):
    session_id = serializers.UUIDField()


class VisitorAppointmentCreateSerializer(serializers.Serializer):
    starts_at = serializers.CharField(max_length=64)
    collected_fields = serializers.JSONField(required=False, default=dict)

    def validate_starts_at(self, value):
        parsed = parse_datetime(value)
        if parsed is None or timezone.is_naive(parsed):
            raise serializers.ValidationError(
                "Use an ISO 8601 datetime with an explicit UTC offset."
            )
        return parsed

    def validate_collected_fields(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be an object.")
        return value


class VisitorAppointmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Appointment
        fields = (
            "id",
            "collected_fields",
            "starts_at",
            "ends_at",
            "status",
            "created_at",
        )
        read_only_fields = fields
