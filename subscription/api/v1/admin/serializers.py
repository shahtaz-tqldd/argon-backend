from rest_framework import serializers

from subscription.models import SubscriptionPlan


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    """Create, update, and represent an administrator-managed plan."""

    class Meta:
        model = SubscriptionPlan
        fields = (
            "id",
            "name",
            "slug",
            "plan_type",
            "ai_message_limit",
            "file_size_limit_mb",
            "knowledge_chunk_limit",
            "ai_message_overage_enabled",
            "features",
            "details_html",
            "is_free",
            "is_public",
            "requires_sales_contact",
            "is_active",
            "sort_order",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "slug",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )

    def validate_features(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Plan features must be unique.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        overage_enabled = attrs.get(
            "ai_message_overage_enabled",
            getattr(self.instance, "ai_message_overage_enabled", False),
        )
        message_limit = attrs.get(
            "ai_message_limit",
            getattr(self.instance, "ai_message_limit", None),
        )

        if overage_enabled and message_limit is None:
            raise serializers.ValidationError(
                {
                    "ai_message_limit": (
                        "An unlimited plan cannot use message overage billing."
                    )
                }
            )
        return attrs
