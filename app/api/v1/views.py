from django.db import transaction
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated

from accounts.permissions import IsSuperAdmin
from app.api.v1.serializers import (
    CONFIG_SECTIONS,
    LEGAL_DOCUMENT_TYPES,
    TourtoiseConfigUpdateSerializer,
    serialize_config_sections,
)
from app.base.models import TourtoiseConfig
from app.utils.response import APIResponse


def get_config():
    config = TourtoiseConfig.objects.first()
    if config is None:
        config = TourtoiseConfig.objects.create()
    return config


class TourtoiseConfigAPIView(GenericAPIView):
    """Return all configuration sections or the sections selected by query param."""

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        selected_sections = [
            section for section in CONFIG_SECTIONS if section in request.query_params
        ]
        document_type = request.query_params.get("document_type")

        if document_type and document_type not in LEGAL_DOCUMENT_TYPES:
            raise serializers.ValidationError(
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


class TourtoiseConfigUpdateAPIView(GenericAPIView):
    """Update the singleton configuration, including multipart logo uploads."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = TourtoiseConfigUpdateSerializer

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        config = get_config()
        serializer = self.get_serializer(
            config,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        config = serializer.save()
        return APIResponse.success(
            data=serialize_config_sections(config, list(CONFIG_SECTIONS)),
            message="Configuration updated successfully.",
        )
