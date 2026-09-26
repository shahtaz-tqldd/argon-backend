from datetime import date

from rest_framework import serializers

from chatbot.models import Chatbot
from workspace.models import Workspace


class AIUsageQuerySerializer(serializers.Serializer):
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


class UserGrowthQuerySerializer(serializers.Serializer):
    month = serializers.RegexField(
        regex=r"^\d{4}-(0[1-9]|1[0-2])$",
        required=False,
        help_text="Optional month in YYYY-MM format.",
    )

    def validate_month(self, value):
        year, month = map(int, value.split("-"))
        if year == 9999:
            raise serializers.ValidationError(
                "Month must be earlier than 9999-01."
            )
        try:
            return date(year, month, 1)
        except ValueError as exc:
            raise serializers.ValidationError(
                "Enter a valid month in YYYY-MM format."
            ) from exc


class AnalyticsFilterSerializer(serializers.Serializer):
    """Shared filters for every superadmin analytics endpoint."""

    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    workspace = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Workspace.objects.all(),
        required=False,
    )
    chatbot = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Chatbot.objects.all(),
        required=False,
    )
    granularity = serializers.ChoiceField(
        choices=("day", "month"),
        default="month",
        required=False,
    )

    def validate(self, attrs):
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError(
                {"end_date": "end_date must be on or after start_date."}
            )

        workspace = attrs.get("workspace")
        chatbot = attrs.get("chatbot")
        if workspace and chatbot and chatbot.workspace_id != workspace.id:
            raise serializers.ValidationError(
                {"chatbot": "The chatbot does not belong to this workspace."}
            )
        return attrs
