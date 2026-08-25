from django.db import migrations, models


def copy_legacy_ai_behavior_settings(apps, schema_editor):
    Chatbot = apps.get_model("chatbot", "Chatbot")
    database = schema_editor.connection.alias

    for chatbot in Chatbot.objects.using(database).iterator():
        settings = chatbot.other_settings
        if not isinstance(settings, dict):
            continue

        updates = {}
        escalation_rule = settings.get("escalation_rule")
        never_answer = settings.get("never_answer")
        if isinstance(escalation_rule, str):
            updates["escalation_rule"] = escalation_rule
        if isinstance(never_answer, str):
            updates["never_answer"] = never_answer
        if updates:
            Chatbot.objects.using(database).filter(pk=chatbot.pk).update(
                **updates
            )


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0007_align_chatbot_model_design"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatbot",
            name="escalation_rule",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="chatbot",
            name="never_answer",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.RunPython(
            copy_legacy_ai_behavior_settings,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
