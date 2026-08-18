import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "vector_store",
            "0002_rename_vector_docu_source__8fdfff_idx_vector_docu_source__b80cad_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="vectordocument",
            name="search_vector",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.SearchVector(
                    models.F("content"),
                    config="english",
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
        migrations.AddIndex(
            model_name="vectordocument",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"],
                name="vector_docs_search_vector_gin",
            ),
        ),
    ]
