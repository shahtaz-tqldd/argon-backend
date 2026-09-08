from collections.abc import Mapping
from uuid import uuid4

from django.conf import settings
from rest_framework import serializers

from app.services.r2 import (
    delete_image, 
    schedule_delete_image, 
    upload_image
)

from base.models import ArgonChatbotConfig
from base.api.v1.utils import CONFIG_SECTIONS


class ArgonChatbotConfigUpdateSerializer(serializers.ModelSerializer):
    logo = serializers.ImageField(write_only=True, required=False)

    class Meta:
        model = ArgonChatbotConfig
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
                folder=f"{settings.R2_IMAGES_PREFIX}/config",
                public_id=f"ArgonChatbot-logo-{uuid4().hex}",
            )
            validated_data["logo"] = upload["url"]

        request = self.context.get("request")
        if request is not None:
            validated_data["updated_by"] = request.user

        try:
            instance = super().update(instance, validated_data)
        except Exception:
            if logo is not None:
                delete_image(public_id=upload["key"])
            raise

        if logo is not None and previous_logo_url and previous_logo_url != instance.logo:
            schedule_delete_image(image_url=previous_logo_url)

        return instance

