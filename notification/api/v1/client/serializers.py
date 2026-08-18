from rest_framework import serializers

from notification.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True, default=False)
    read_at = serializers.DateTimeField(read_only=True, allow_null=True, default=None)
    workspace_id = serializers.UUIDField(read_only=True, allow_null=True)
    chatbot_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "recipient_type",
            "notification_type",
            "title",
            "message",
            "metadata",
            "workspace_id",
            "chatbot_id",
            "target_id",
            "is_read",
            "read_at",
            "created_at",
        ]
