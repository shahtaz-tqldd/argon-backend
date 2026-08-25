from django.db import migrations


def ensure_default_vector_table(apps, schema_editor):
    """Handle installs where the old router recorded a no-op migration."""

    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS vector")
    VectorDocument = apps.get_model("vector_store", "VectorDocument")
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        table_names = connection.introspection.table_names(cursor)
    if VectorDocument._meta.db_table not in table_names:
        schema_editor.create_model(VectorDocument)


class Migration(migrations.Migration):
    dependencies = [
        ("vector_store", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            ensure_default_vector_table,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
