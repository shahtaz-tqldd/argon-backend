import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings
from rest_framework import serializers

from chat.utils.choices import ChatMessageAttachmentType


DOCUMENT_MIME_PREFIXES = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument",
    "application/json",
    "application/rtf",
    "application/zip",
    "text/",
)


def classify_attachment_type(mime_type):
    if mime_type.startswith("image/"):
        return ChatMessageAttachmentType.IMAGE
    if mime_type.startswith("video/"):
        return ChatMessageAttachmentType.VIDEO
    if mime_type.startswith("audio/"):
        return ChatMessageAttachmentType.AUDIO
    if mime_type.startswith(DOCUMENT_MIME_PREFIXES):
        return ChatMessageAttachmentType.DOCUMENT
    return ChatMessageAttachmentType.OTHER


class MessageAttachmentInputSerializer(serializers.Serializer):
    """A pre-uploaded file referenced by URL.

    Files are uploaded through the base file API first; message creation
    only stores the attachment metadata.
    """

    file_url = serializers.URLField(max_length=2048)
    file_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
    )
    mime_type = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    file_size = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
    )
    duration_ms = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
    )
    attachment_type = serializers.ChoiceField(
        choices=ChatMessageAttachmentType.choices,
        required=False,
    )

    def validate(self, attrs):
        mime_type = (attrs.get("mime_type") or "").lower()
        if not mime_type:
            mime_type = (
                mimetypes.guess_type(urlparse(attrs["file_url"]).path)[0]
                or ""
            )
            if mime_type:
                attrs["mime_type"] = mime_type

        if not attrs.get("attachment_type"):
            attrs["attachment_type"] = classify_attachment_type(mime_type)

        if not attrs.get("file_name"):
            attrs["file_name"] = Path(
                unquote(urlparse(attrs["file_url"]).path)
            ).name[:255]

        return attrs


def message_attachment_field():
    return serializers.ListField(
        child=MessageAttachmentInputSerializer(),
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
    for sort_order, attachment in enumerate(attachments):
        attachment["sort_order"] = sort_order
        if (attachment.get("file_size") or 0) > max_bytes:
            raise serializers.ValidationError(
                {
                    "attachments": [
                        "Each attachment cannot exceed "
                        f"{settings.CHAT_ATTACHMENT_MAX_FILE_SIZE_MB} MB."
                    ]
                }
            )
    return attrs
