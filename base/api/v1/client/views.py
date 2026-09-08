from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.serializers import ValidationError

from base.api.v1.utils import (
    CONFIG_SECTIONS,
    LEGAL_DOCUMENT_TYPES,
    serialize_config_sections,
)

from base.models import ArgonChatbotConfig

from app.utils.response import APIResponse


def get_config():
    config = ArgonChatbotConfig.objects.first()
    if config is None:
        config = ArgonChatbotConfig.objects.create()
    return config


class ArgonChatbotConfigAPIView(GenericAPIView):
    """Return all configuration sections or the sections selected by query param."""

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        selected_sections = [
            section for section in CONFIG_SECTIONS if section in request.query_params
        ]
        document_type = request.query_params.get("document_type")

        if document_type and document_type not in LEGAL_DOCUMENT_TYPES:
            raise ValidationError(
                {"document_type": f"Choose one of: {', '.join(LEGAL_DOCUMENT_TYPES)}."}
            )

        if document_type and "legal_document" not in selected_sections:
            selected_sections.append("legal_document")

        if not selected_sections:
            selected_sections = list(CONFIG_SECTIONS)

        config = get_config()
        return APIResponse.success(
            data=serialize_config_sections(config, selected_sections, document_type),
            message="Configuration fetched successfully.",
        )

