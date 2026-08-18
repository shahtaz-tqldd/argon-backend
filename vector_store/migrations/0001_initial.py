import uuid

import pgvector.django
from django.db import migrations, models
from django.contrib.postgres.indexes import GinIndex
from pgvector.django import HnswIndex, VectorExtension


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        VectorExtension(),
        migrations.CreateModel(
            name="VectorDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_type", models.CharField(choices=[("destination", "Destination"), ("attraction", "Attraction"), ("activity", "Activity"), ("cuisine", "Cuisine")], max_length=20)),
                ("source_id", models.UUIDField()),
                ("content", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("embedding", pgvector.django.VectorField(dimensions=1536)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "vector_documents",
                "ordering": ["source_type", "source_id", "created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="vectordocument",
            index=models.Index(fields=["source_type", "source_id"], name="vector_docu_source__8fdfff_idx"),
        ),
        migrations.AddIndex(
            model_name="vectordocument",
            index=GinIndex(fields=["metadata"], name="vector_docu_metadat_1e173d_gin"),
        ),
        migrations.AddIndex(
            model_name="vectordocument",
            index=HnswIndex(
                ef_construction=64,
                fields=["embedding"],
                m=16,
                name="vector_docs_embedding_hnsw",
                opclasses=["vector_cosine_ops"],
            ),
        ),
    ]
