import mimetypes
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from rest_framework import serializers

from app.services.r2 import R2Storage, upload_file, upload_image
from app.utils.logger import logger
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


def upload_message_attachments(files, *, chatbot_id=None, storage=None):
    """Upload request files to R2 and return ChatMessageAttachment payloads.

    Images are optimized through upload_image; every other file is stored
    unchanged. The returned dicts carry the ChatMessageAttachment field
    values plus an ``object_key`` used to delete orphaned uploads when the
    message row is never created.
    """

    storage = storage or R2Storage()
    attachments = []
    for sort_order, uploaded_file in enumerate(files):
        original_name = Path(getattr(uploaded_file, "name", "") or "attachment").name
        mime_type = (
            getattr(uploaded_file, "content_type", None)
            or mimetypes.guess_type(original_name)[0]
            or ""
        ).lower()
        attachment_type = classify_attachment_type(mime_type)
        public_id = uuid4().hex
        if attachment_type == ChatMessageAttachmentType.IMAGE:
            upload = upload_image(
                uploaded_file,
                folder=_chat_folder(settings.R2_IMAGES_PREFIX, chatbot_id),
                public_id=public_id,
                storage=storage,
            )
            stored_mime_type = mimetypes.guess_type(upload["key"])[0] or mime_type
        else:
            upload = upload_file(
                uploaded_file,
                folder=_chat_folder(settings.R2_FILES_PREFIX, chatbot_id),
                public_id=public_id,
                storage=storage,
            )
            stored_mime_type = upload["content_type"] or mime_type
        attachments.append(
            {
                "attachment_type": attachment_type,
                "file_url": upload["url"],
                "file_name": original_name[:255],
                "mime_type": stored_mime_type[:100],
                "file_size": getattr(uploaded_file, "size", None),
                "sort_order": sort_order,
                "object_key": upload["key"],
            }
        )
    return attachments


def delete_message_attachment_uploads(attachments, *, storage=None):
    """Remove R2 objects for attachment payloads that got no message row."""

    if not attachments:
        return
    storage = storage or R2Storage()
    for attachment in attachments:
        object_key = attachment.get("object_key")
        if not object_key:
            continue
        try:
            storage.delete(object_key)
        except Exception:
            logger.exception(
                "Could not delete orphaned chat attachment %s",
                object_key,
            )


def _chat_folder(prefix, chatbot_id):
    folder = f"{(prefix or '').strip('/')}/chat"
    if chatbot_id:
        folder = f"{folder}/{chatbot_id}"
    return folder.strip("/")




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
