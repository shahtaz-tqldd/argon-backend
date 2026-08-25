from rest_framework import serializers

from vector_store.models import VectorDocument


class VectorRecordSerializer(serializers.ModelSerializer):
    knowledge_base_id = serializers.UUIDField(read_only=True)
    chatbot_id = serializers.UUIDField(
        source="knowledge_base.chatbot_id",
        read_only=True,
    )

    class Meta:
        model = VectorDocument
        fields = (
            "id",
            "knowledge_base_id",
            "chatbot_id",
            "chunk_index",
            "token_count",
            "content_hash",
            "content",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class VectorRecordListQuerySerializer(serializers.Serializer):
    chatbot_id = serializers.UUIDField(required=False)
    knowledge_base_id = serializers.UUIDField(required=False)


class VectorSemanticSearchQuerySerializer(serializers.Serializer):
    query = serializers.CharField(max_length=2000, allow_blank=False)
    chatbot_id = serializers.UUIDField(required=False)
    knowledge_base_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=False,
        max_length=500,
    )
    limit = serializers.IntegerField(min_value=1, max_value=100, default=10)

    def validate_knowledge_base_ids(self, value):
        return list(dict.fromkeys(value))


class VectorSearchResultSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    knowledge_base_id = serializers.UUIDField(read_only=True)
    chatbot_id = serializers.UUIDField(read_only=True)
    chunk_index = serializers.IntegerField(read_only=True)
    token_count = serializers.IntegerField(read_only=True)
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
