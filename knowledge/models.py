from django.db import models
from django.db.models import Q

from app.base.models import BaseModel, BaseMinModel
from knowledge.utils.choices import (
    KnowledgeSourceTypes, 
    KnowledgeTrainingStageTypes,
    StatusTypes,
)


class KnowledgeBase(BaseModel):
    """A single file, website, or custom-text knowledge source."""

    NAME_MAX_LENGTH = 80

    chatbot = models.ForeignKey(
        "chatbot.Chatbot",
        related_name="knowledge_bases",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=500, blank=True)
    source_type = models.CharField(
        max_length=20,
        choices=KnowledgeSourceTypes.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=StatusTypes.choices,
        default=StatusTypes.PENDING,
        db_index=True,
    )
    is_enabled = models.BooleanField(default=True, db_index=True)

    # Website source
    url = models.URLField(blank=True, null=True)
    last_crawled_at = models.DateTimeField(blank=True, null=True)

    # File source. Only the private object key is persisted; clients receive a
    # short-lived presigned URL generated at serialization time.
    original_filename = models.CharField(max_length=255, blank=True)
    file_type = models.CharField(max_length=20, blank=True)
    file_key = models.CharField(max_length=1024, blank=True)
    file_size = models.PositiveBigIntegerField(blank=True, null=True)

    # User-entered text is retained so it can be edited. Extracted content is
    # the normalized input used by the vector training pipeline for all types.
    text_content = models.TextField(blank=True)
    extracted_content = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)

    error_message = models.TextField(blank=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    @property
    def name(self):
        if self.title:
            value = self.title
        elif self.source_type == KnowledgeSourceTypes.FILE:
            value = self.original_filename
        elif self.source_type == KnowledgeSourceTypes.WEBSITE:
            value = self.url or ""
        elif self.source_type == KnowledgeSourceTypes.TEXT:
            value = self.text_content or ""
        else:
            value = ""

        value = " ".join(value.split())
        if len(value) > self.NAME_MAX_LENGTH:
            return f"{value[:self.NAME_MAX_LENGTH - 3].rstrip()}..."
        return value or "Untitled knowledge source"

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chatbot", "source_type"], name="kb_chatbot_source_idx"),
            models.Index(fields=["chatbot", "status"], name="kb_chatbot_status_idx"),
            models.Index(fields=["chatbot", "is_enabled"], name="kb_chatbot_enabled_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~Q(source_type=KnowledgeSourceTypes.WEBSITE)
                    | (Q(url__isnull=False) & ~Q(url=""))
                ),
                name="kb_website_requires_url",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(source_type=KnowledgeSourceTypes.FILE) | ~Q(file_key="")
                ),
                name="kb_file_requires_key",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(source_type=KnowledgeSourceTypes.TEXT) | ~Q(text_content="")
                ),
                name="kb_text_requires_content",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()})"


class KnowledgeTrainingLog(BaseMinModel):
    """Durable progress and audit record for one training/retraining run."""

    knowledge_base = models.ForeignKey(
        KnowledgeBase,
        related_name="training_logs",
        on_delete=models.CASCADE,
    )
    celery_task_id = models.CharField(max_length=255, blank=True, db_index=True)
    stage = models.CharField(
        max_length=20,
        choices=KnowledgeTrainingStageTypes.choices,
        default=KnowledgeTrainingStageTypes.QUEUED,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    total_chunks = models.PositiveIntegerField(default=0)
    processed_chunks = models.PositiveIntegerField(default=0)
    force_retrain = models.BooleanField(default=False)
    content_changed = models.BooleanField(null=True, blank=True)
    message = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["knowledge_base", "stage"],
                name="kb_log_source_stage_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["knowledge_base"],
                condition=Q(
                    stage__in=[
                        "queued",
                        "extracting",
                        "chunking",
                        "embedding",
                        "indexing",
                    ]
                ),
                name="unique_active_training_per_source",
            ),
        ]

    def __str__(self):
        return f"{self.knowledge_base.name}: {self.stage} ({self.progress}%)"
