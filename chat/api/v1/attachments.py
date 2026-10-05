from django.conf import settings
from rest_framework import serializers


def message_attachment_field():
    return serializers.ListField(
        child=serializers.FileField(),
        required=False,
        default=list,
        max_length=settings.CHAT_ATTACHMENT_MAX_COUNT,
    )


def validate_message_payload(attrs):
    """Shared create-message checks: blank rules and per-file size cap."""

    content = attrs.get("content") or ""
    attachments = attrs.get("attachments") or []
    if not content.strip() and not attachments:
        raise serializers.ValidationError(
            {"content": ["Message content cannot be blank without an attachment."]}
        )
    max_bytes = settings.CHAT_ATTACHMENT_MAX_FILE_SIZE_MB * 1024 * 1024
    if any(
        getattr(attachment, "size", 0) > max_bytes for attachment in attachments
    ):
        raise serializers.ValidationError(
            {
                "attachments": [
                    "Each attachment cannot exceed "
                    f"{settings.CHAT_ATTACHMENT_MAX_FILE_SIZE_MB} MB."
                ]
            }
        )
    return attrs
