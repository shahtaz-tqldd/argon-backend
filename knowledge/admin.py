from django.contrib import admin

from knowledge.models import KnowledgeBase, KnowledgeTrainingLog


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "chatbot",
        "source_type",
        "status",
        "is_enabled",
        "updated_at",
    )
    list_filter = ("source_type", "status", "is_enabled")
    search_fields = ("title", "url", "original_filename", "chatbot__name")
    readonly_fields = ("content_hash", "processed_at", "created_at", "updated_at")


@admin.register(KnowledgeTrainingLog)
class KnowledgeTrainingLogAdmin(admin.ModelAdmin):
    list_display = (
        "knowledge_base",
        "stage",
        "progress",
        "processed_chunks",
        "total_chunks",
        "created_at",
    )
    list_filter = ("stage", "force_retrain", "content_changed")
    search_fields = ("knowledge_base__title", "celery_task_id", "error_message")
    readonly_fields = ("created_at", "updated_at", "started_at", "completed_at")
