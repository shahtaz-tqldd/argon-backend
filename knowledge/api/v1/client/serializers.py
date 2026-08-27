from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers

from knowledge.models import KnowledgeBase, KnowledgeTrainingLog
from knowledge.services.storage import PrivateKnowledgeStorage
from knowledge.services.validation import (
    validate_custom_text,
    validate_knowledge_file,
    validate_public_url,
)
from knowledge.services.usage import (
    KnowledgeEntitlementError,
    KnowledgeLimitExceeded,
    validate_knowledge_file_capacity,
)
from knowledge.utils.choices import KnowledgeSourceTypes


KNOWLEDGE_API_TYPES = ("file", "url", "custom")
KNOWLEDGE_API_TYPE_TO_SOURCE_TYPE = {
    "file": KnowledgeSourceTypes.FILE,
    "url": KnowledgeSourceTypes.WEBSITE,
    "custom": KnowledgeSourceTypes.TEXT,
}


class KnowledgeChatbotQuerySerializer(serializers.Serializer):
    chatbot = serializers.SlugField()


class KnowledgeUsageSerializer(serializers.Serializer):
    total_chunks = serializers.IntegerField(read_only=True)
    chunk_limit = serializers.IntegerField(read_only=True)
    total_file_size_bytes = serializers.IntegerField(read_only=True)
    file_size_limit_bytes = serializers.IntegerField(read_only=True)
    file_size_limit_mb = serializers.IntegerField(read_only=True)


class KnowledgeUploadQuerySerializer(KnowledgeChatbotQuerySerializer):
    type = serializers.ChoiceField(choices=KNOWLEDGE_API_TYPES)


class KnowledgeBaseQuerySerializer(serializers.Serializer):
    knowledge_base_id = serializers.UUIDField()


class KnowledgeUpdateQuerySerializer(KnowledgeBaseQuerySerializer):
    type = serializers.ChoiceField(choices=KNOWLEDGE_API_TYPES)


class KnowledgeTrainingLogSerializer(serializers.ModelSerializer):
    knowledge_base_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = KnowledgeTrainingLog
        fields = (
            "id",
            "knowledge_base_id",
            "celery_task_id",
            "stage",
            "progress",
            "total_chunks",
            "processed_chunks",
            "force_retrain",
            "content_changed",
            "message",
            "error_message",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class KnowledgeBaseBasicSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)

    class Meta:
        model = KnowledgeBase
        fields = (
            "id",
            "name",
            "url",
            "source_type",
            "file_type",
            "file_size",
            "is_enabled",
            "status",
            "last_crawled_at",
            "processed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class KnowledgeBaseSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    file_url = serializers.SerializerMethodField()
    latest_training = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeBase
        fields = (
            "id",
            "name",
            "title",
            "source_type",
            "status",
            "is_enabled",
            "url",
            "last_crawled_at",
            "original_filename",
            "file_type",
            "file_url",
            "file_size",
            "text_content",
            "content_hash",
            "error_message",
            "processed_at",
            "latest_training",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_file_url(self, instance):
        if not instance.file_key:
            return None
        try:
            return PrivateKnowledgeStorage().private_url(instance.file_key)
        except ImproperlyConfigured:
            return None

    def get_latest_training(self, instance):
        logs = getattr(instance, "all_training_logs", None)
        latest = logs[0] if logs else instance.training_logs.first()
        if latest is None:
            return None
        return KnowledgeTrainingLogSerializer(latest).data


class FileKnowledgeCreateSerializer(serializers.Serializer):
    file = serializers.FileField(
        write_only=True,
        validators=[validate_knowledge_file],
    )
    title = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        try:
            validate_knowledge_file_capacity(
                self.context["chatbot"],
                attrs["file"].size,
            )
        except (KnowledgeEntitlementError, KnowledgeLimitExceeded) as exc:
            raise serializers.ValidationError({"file": [str(exc)]}) from exc
        return attrs

    def create(self, validated_data):
        uploaded_file = validated_data["file"]
        chatbot = self.context["chatbot"]
        storage = self.context.get("storage") or PrivateKnowledgeStorage()
        key = storage.build_key(
            chatbot_id=chatbot.id,
            filename=uploaded_file.name,
        )
        storage.upload(
            uploaded_file,
            key=key,
            content_type=getattr(uploaded_file, "content_type", None),
        )
        try:
            return KnowledgeBase.objects.create(
                chatbot=chatbot,
                title=validated_data.get("title", ""),
                source_type=KnowledgeSourceTypes.FILE,
                original_filename=Path(uploaded_file.name).name,
                file_type=Path(uploaded_file.name).suffix.lower().lstrip("."),
                file_key=key,
                file_size=uploaded_file.size,
                created_by=self.context["request"].user,
                updated_by=self.context["request"].user,
            )
        except Exception:
            storage.delete(key)
            raise


class URLKnowledgeCreateSerializer(serializers.Serializer):
    url = serializers.URLField(validators=[validate_public_url])
    title = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def create(self, validated_data):
        return KnowledgeBase.objects.create(
            chatbot=self.context["chatbot"],
            title=validated_data.get("title", ""),
            source_type=KnowledgeSourceTypes.WEBSITE,
            url=validated_data["url"],
            created_by=self.context["request"].user,
            updated_by=self.context["request"].user,
        )


class TextKnowledgeCreateSerializer(serializers.Serializer):
    content = serializers.CharField(
        write_only=True,
        trim_whitespace=True,
        validators=[validate_custom_text],
    )
    title = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def create(self, validated_data):
        return KnowledgeBase.objects.create(
            chatbot=self.context["chatbot"],
            title=validated_data.get("title", ""),
            source_type=KnowledgeSourceTypes.TEXT,
            text_content=validated_data["content"],
            created_by=self.context["request"].user,
            updated_by=self.context["request"].user,
        )


class KnowledgeMetadataUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=500, required=False, allow_blank=True)
    is_enabled = serializers.BooleanField(required=False)

    def update(self, instance, validated_data):
        if "title" in validated_data:
            instance.title = validated_data["title"]
        if "is_enabled" in validated_data:
            instance.is_enabled = validated_data["is_enabled"]
        instance.updated_by = self.context["request"].user
        instance.save()
        return instance


class TextKnowledgeUpdateSerializer(KnowledgeMetadataUpdateSerializer):
    content = serializers.CharField(
        required=False,
        trim_whitespace=True,
        validators=[validate_custom_text],
    )

    def update(self, instance, validated_data):
        if "content" in validated_data:
            instance.text_content = validated_data["content"]
        return super().update(instance, validated_data)
