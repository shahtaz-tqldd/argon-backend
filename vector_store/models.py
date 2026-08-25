from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from app.base.models import BaseMinModel


class VectorDocument(BaseMinModel):
    knowledge_base = models.ForeignKey(
        "knowledge.KnowledgeBase",
        related_name="vector_documents",
        on_delete=models.CASCADE,
    )
    chunk_index = models.PositiveIntegerField(default=0)
    token_count = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    embedding = VectorField(dimensions=1536)
    search_vector = models.GeneratedField(
        expression=SearchVector("content", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "vector_documents"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["knowledge_base", "chunk_index"],
                name="unique_knowledge_vector_chunk",
            ),
        ]
        indexes = [
            models.Index(
                fields=["knowledge_base", "chunk_index"],
                name="vector_kb_chunk_idx",
            ),
            GinIndex(fields=["metadata"]),
            GinIndex(name="vector_docs_search_vector_gin", fields=["search_vector"]),
            HnswIndex(
                name="vector_docs_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.knowledge_base.name}:{self.chunk_index}"
