from rest_framework import serializers

from vector_store.models import VectorDocument


class SourceTypeFilterField(serializers.Field):
    default_error_messages = {
        "invalid": "Send source_type as a value or list of values.",
        "invalid_choice": "Unsupported source type: {value}.",
        "empty": "Provide at least one source type.",
    }

    aliases = {
        "destination": VectorDocument.SourceType.DESTINATION,
        "destinations": VectorDocument.SourceType.DESTINATION,
        "attraction": VectorDocument.SourceType.ATTRACTION,
        "attractions": VectorDocument.SourceType.ATTRACTION,
        "activity": VectorDocument.SourceType.ACTIVITY,
        "activities": VectorDocument.SourceType.ACTIVITY,
        "cuisine": VectorDocument.SourceType.CUISINE,
        "cuisines": VectorDocument.SourceType.CUISINE,
    }

    def to_internal_value(self, data):
        if isinstance(data, str):
            values = data.split(",")
        elif isinstance(data, (list, tuple)):
            values = data
        else:
            self.fail("invalid")

        normalized = []
        for value in values:
            value = str(value).strip().lower()
            if not value:
                continue
            source_type = self.aliases.get(value)
            if source_type is None:
                self.fail("invalid_choice", value=value)
            if source_type not in normalized:
                normalized.append(source_type)

        if not normalized:
            self.fail("empty")
        return normalized

    def to_representation(self, value):
        return list(value)


class VectorRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = VectorDocument
        fields = (
            "id",
            "source_type",
            "source_id",
            "content",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class VectorRecordListQuerySerializer(serializers.Serializer):
    source_type = SourceTypeFilterField(required=False)
    destination_id = serializers.UUIDField(required=False)
    source_id = serializers.UUIDField(required=False)


class VectorSemanticSearchQuerySerializer(serializers.Serializer):
    query = serializers.CharField(max_length=2000, allow_blank=False)
    source_type = SourceTypeFilterField(required=False)
    destination_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=10)


class VectorSearchResultSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    source_type = serializers.ChoiceField(
        choices=VectorDocument.SourceType.choices,
        read_only=True,
    )
    source_id = serializers.UUIDField(read_only=True)
    content = serializers.CharField(read_only=True)
    metadata = serializers.JSONField(read_only=True)
    distance = serializers.FloatField(read_only=True, allow_null=True)
    text_rank = serializers.FloatField(read_only=True, allow_null=True)
    rrf_score = serializers.FloatField(read_only=True)


class VectorRecordBulkDeleteSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=500,
    )

    def validate_ids(self, value):
        return list(dict.fromkeys(value))
