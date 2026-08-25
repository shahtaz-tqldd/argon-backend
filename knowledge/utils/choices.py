from django.db import models

class KnowledgeSourceTypes(models.TextChoices):
    FILE = "file", "File"
    WEBSITE = "website", "Website"
    TEXT = "text", "Text"

class StatusTypes(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"

class KnowledgeTrainingStageTypes(models.TextChoices):
    QUEUED = "queued", "Queued"
    EXTRACTING = "extracting", "Extracting"
    CHUNKING = "chunking", "Chunking"
    EMBEDDING = "embedding", "Embedding"
    INDEXING = "indexing", "Indexing"
    COMPLETED = "completed", "Completed"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"