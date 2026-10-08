from django.utils import timezone
from rest_framework import serializers

from subscription.choices import EnterprisePlanRequestStatus, PlanFeature
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
            "team_members_limit",
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


class EnterprisePlanRequestFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=EnterprisePlanRequestStatus.choices,
        required=False,
    )
    chatbot = serializers.SlugField(required=False)


class EnterprisePlanRequestApproveSerializer(serializers.Serializer):
    features = serializers.ListField(
        child=serializers.ChoiceField(choices=PlanFeature.choices),
        required=False,
        default=list,
        allow_empty=True,
    )
    ai_message_limit = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    file_size_limit_mb = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    knowledge_chunk_limit = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=1
    )
    expires_at = serializers.DateTimeField(required=True)
    review_notes = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
    )

    def validate_features(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Features must be unique.")
        return value

    def validate_expires_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError(
                "The expiry date must be in the future."
            )
        return value


class EnterprisePlanRequestSubscriptionSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    current_period_end = serializers.DateTimeField(read_only=True)
    ai_message_limit = serializers.SerializerMethodField()
    file_size_limit_mb = serializers.SerializerMethodField()
    knowledge_chunk_limit = serializers.SerializerMethodField()
    features = serializers.SerializerMethodField()

    def get_ai_message_limit(self, obj):
        return obj.get_ai_message_limit()

    def get_file_size_limit_mb(self, obj):
        return obj.get_file_size_limit_mb()

    def get_knowledge_chunk_limit(self, obj):
        return obj.get_knowledge_chunk_limit()

    def get_features(self, obj):
        return obj.get_features()
