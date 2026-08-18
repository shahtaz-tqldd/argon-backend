from django.db import models
from app.base.models import BaseModel


# Create your models here.
class KnowledgeBase(BaseModel):
    chatbot = models.ForeignKey(
        "chatbots.Chatbot",
        related_name="knowledge_bases",
        on_delete=models.CASCADE,
    )

    name = models.CharField(max_length=200, default="Default Knowledge Base")
    is_enabled = models.BooleanField(default=True)

    class SourceType(models.TextChoices):
        FILE = "file", "File"
        WEBSITE = "website", "Website"
        TEXT = "text", "Text"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    # for website
    url = models.URLField()
    title = models.CharField(max_length=500, blank=True)
    last_crawled_at = models.DateTimeField(null=True, blank=True)


    # for file
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(
        max_length=20,
        # pdf, docx, pptx, xlsx, txt, etc.
    )
    file_url = models.URLField()
    file_size = models.BigIntegerField(default=0)

    # for custom text
    text_content = models.TextField(blank=True)

    