from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework import serializers

from app.services.r2 import delete_image, schedule_delete_image, upload_image
from app.utils.storage_fields import R2ImageField
from workspace.models import Workspace


User = get_user_model()


class WorkspaceQuerySerializer(serializers.Serializer):
    workspace = serializers.SlugField()


class WorkspaceOwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "name")
        read_only_fields = fields


class WorkspaceBaseSerializer(serializers.ModelSerializer):
    logo = R2ImageField(required=False)
    clear_logo = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
    )
    owner = WorkspaceOwnerSerializer(read_only=True)

    class Meta:
        model = Workspace
        fields = (
            "id",
            "name",
            "slug",
            "logo",
            "clear_logo",
            "industry",
            "owner",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "slug",
            "owner",
            "is_active",
            "created_at",
            "updated_at",
        )

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("This field is required.")
        return value

    def validate(self, attrs):
        if attrs.get("logo") and attrs.get("clear_logo"):
            raise serializers.ValidationError(
                {"clear_logo": "Cannot clear and replace the logo together."}
            )
        return attrs

    def create(self, validated_data):
        logo = validated_data.pop("logo", None)
        validated_data.pop("clear_logo", False)
        upload = None
        if logo is not None:
            upload = upload_image(
                logo,
                folder=f"{settings.R2_IMAGES_PREFIX}/workspaces",
                public_id=f"workspace-{uuid4().hex}",
            )
            validated_data["logo"] = upload["url"]

        user = self.context["request"].user
        try:
            with transaction.atomic():
                workspace = Workspace.objects.create(
                    owner=user,
                    created_by=user,
                    **validated_data,
                )

        except Exception:
            if upload is not None:
                delete_image(public_id=upload["key"])
            raise
        return workspace

    def update(self, instance, validated_data):
        logo = validated_data.pop("logo", None)
        clear_logo = validated_data.pop("clear_logo", False)
        previous_logo_url = instance.logo
        upload = None

        if logo is not None:
            upload = upload_image(
                logo,
                folder=f"{settings.R2_IMAGES_PREFIX}/workspaces",
                public_id=f"workspace-{instance.pk}-{uuid4().hex}",
            )
            validated_data["logo"] = upload["url"]
        elif clear_logo:
            validated_data["logo"] = ""

        instance.updated_by = self.context["request"].user
        try:
            with transaction.atomic():
                instance = super().update(instance, validated_data)
        except Exception:
            if upload is not None:
                delete_image(public_id=upload["key"])
            raise

        if previous_logo_url and previous_logo_url != instance.logo:
            schedule_delete_image(image_url=previous_logo_url)
        return instance


class WorkspaceListSerializer(WorkspaceBaseSerializer):
    """Serialize workspaces returned by the list endpoint."""


class WorkspaceCreateSerializer(WorkspaceBaseSerializer):
    """Validate and serialize workspace creation."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        user = self.context["request"].user
        if Workspace.objects.filter(owner=user).exists():
            raise serializers.ValidationError(
                "Each user can create only one workspace."
            )
        return attrs

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError(
                "Each user can create only one workspace."
            ) from exc


class WorkspaceDetailSerializer(WorkspaceBaseSerializer):
    """Serialize complete workspace details."""


class WorkspaceUpdateSerializer(WorkspaceBaseSerializer):
    """Validate and serialize workspace updates."""


class WorkspaceDeleteSerializer(serializers.Serializer):
    """Represent the body-less workspace delete operation."""


class WorkspaceSerializer(WorkspaceDetailSerializer):
    """Backward-compatible alias for the original public serializer."""

