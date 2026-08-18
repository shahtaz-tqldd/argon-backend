from collections.abc import Mapping
from uuid import uuid4

from django.conf import settings
from rest_framework import serializers

from app.base.models import TourtoiseConfig
from app.utils.cloudinary import delete_image, upload_image


CONFIG_SECTIONS = {
    "branding": ("title", "logo", "favicon", "support_email"),
    "legal_document": (
        "privacy_policy",
        "terms_of_service",
        "data_deletion_policy",
        "cookie_policy",
    ),
    "feature": ("is_vectorize_enabled", "maintenance_mode"),
    "announcement": (
        "notify_banner_enabled",
        "notify_banner_text",
        "notify_banner_url",
    ),
    "platform_default": ("default_free_credits", "monthly_free_credits"),
    "seo": ("meta_title", "meta_description"),
    "audit": ("created_at", "updated_at", "updated_by"),
}

LEGAL_DOCUMENT_TYPES = CONFIG_SECTIONS["legal_document"]


class TourtoiseConfigUpdateSerializer(serializers.ModelSerializer):
    logo = serializers.ImageField(write_only=True, required=False)

    class Meta:
        model = TourtoiseConfig
        fields = tuple(
            field
            for section, section_fields in CONFIG_SECTIONS.items()
            if section != "audit"
            for field in section_fields
        )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            return super().to_internal_value(data)

        normalized_data = data.copy()
        for section, fields in CONFIG_SECTIONS.items():
            if section == "audit" or section not in data:
                continue

            section_data = data[section]
            if not isinstance(section_data, Mapping):
                raise serializers.ValidationError(
                    {section: "Expected an object containing configuration fields."}
                )

            for field in fields:
                if field in section_data:
                    normalized_data[field] = section_data[field]

        return super().to_internal_value(normalized_data)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one configuration field to update.")
        return attrs

    def update(self, instance, validated_data):
        logo = validated_data.pop("logo", None)
        previous_logo_url = instance.logo

        if logo is not None:
            upload = upload_image(
                logo,
                folder=f"{settings.CLOUDINARY_FOLDER}/config",
                public_id=f"tourtoise-logo-{uuid4().hex}",
            )
            validated_data["logo"] = upload["url"]

        request = self.context.get("request")
        if request is not None:
            validated_data["updated_by"] = request.user

        instance = super().update(instance, validated_data)

        if logo is not None and previous_logo_url and previous_logo_url != instance.logo:
            delete_image(image_url=previous_logo_url)

        return instance


def serialize_config_sections(instance, sections, document_type=None):
    data = {}
    for section in sections:
        fields = CONFIG_SECTIONS[section]
        if section == "legal_document" and document_type:
            fields = (document_type,)

        values = {}
        for field in fields:
            value = getattr(instance, field)
            if field == "updated_by":
                value = str(value.pk) if value else None
            elif field in ("created_at", "updated_at"):
                value = value.isoformat() if value else None
            values[field] = value
        data[section] = values
    return data
