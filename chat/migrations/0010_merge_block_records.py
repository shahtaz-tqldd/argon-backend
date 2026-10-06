from django.db import migrations


def merge_block_records_into_visitors(apps, schema_editor):
    """Fold ChatbotBlockedVisitor rows into ChatbotVisitor.is_blocked."""
    ChatbotBlockedVisitor = apps.get_model(
        "chat_session",
        "ChatbotBlockedVisitor",
    )
    ChatbotVisitor = apps.get_model(
        "chatbot",
        "ChatbotVisitor",
    )

    for record in (
        ChatbotBlockedVisitor.objects
        .select_related("visitor")
        .all()
    ):
        visitor = record.visitor

        visitor.is_blocked = True
        visitor.blocked_at = record.created_at
        visitor.blocked_by_id = record.blocked_by_id

        visitor.save(
            update_fields=[
                "is_blocked",
                "blocked_at",
                "blocked_by",
                "updated_at",
            ],
        )


def restore_block_records(apps, schema_editor):
    """Best-effort reverse: recreate block records."""
    ChatbotBlockedVisitor = apps.get_model(
        "chat_session",
        "ChatbotBlockedVisitor",
    )
    ChatbotVisitor = apps.get_model(
        "chatbot",
        "ChatbotVisitor",
    )

    for visitor in ChatbotVisitor.objects.filter(
        is_blocked=True,
    ):
        ChatbotBlockedVisitor.objects.get_or_create(
            visitor_id=visitor.pk,
            defaults={
                "blocked_by_id": visitor.blocked_by_id,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("chat_session", "0009_visitor_sessions_constraints"),
        ("chatbot", "0020_chatbotvisitor_blocking"),
    ]

    operations = [
        migrations.RunPython(
            merge_block_records_into_visitors,
            reverse_code=restore_block_records,
        ),
    ]