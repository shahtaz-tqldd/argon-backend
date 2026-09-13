from django.db import migrations, models


LEGACY_REASON_TEXT = {
    "human_requested": "Visitor requested human support.",
    "ai_uncertain": "AI could not answer confidently.",
    "tool_failed": "A required agent tool failed.",
    "escalated": "Escalated by the AI assistant.",
    "other": "Human review requested.",
}


def expand_legacy_attention_reasons(apps, schema_editor):
    ChatSession = apps.get_model("chat_session", "ChatSession")
    for legacy_value, reason_text in LEGACY_REASON_TEXT.items():
        ChatSession.objects.filter(attention_reason=legacy_value).update(
            attention_reason=reason_text,
        )


def restore_legacy_attention_reasons(apps, schema_editor):
    ChatSession = apps.get_model("chat_session", "ChatSession")
    for legacy_value, reason_text in LEGACY_REASON_TEXT.items():
        ChatSession.objects.filter(attention_reason=reason_text).update(
            attention_reason=legacy_value,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("chat_session", "0002_chatsessiontransfer_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatsession",
            name="attention_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunPython(
            expand_legacy_attention_reasons,
            restore_legacy_attention_reasons,
        ),
    ]
