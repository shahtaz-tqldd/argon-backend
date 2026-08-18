import uuid

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from app.base.models import BaseMinModel
from pgvector.django import HnswIndex, VectorField


class VectorDocument(BaseMinModel):
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
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            GinIndex(fields=["metadata"]),
            GinIndex(
                name="vector_docs_search_vector_gin",
                fields=["search_vector"],
            ),
            HnswIndex(
                name="vector_docs_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    # def __str__(self):
    #     return f"{self.source_type}:{self.source_id}"
