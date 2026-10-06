import django.db.models.deletion
from django.db import migrations, models


def keep_one_visitor_per_lead(apps, schema_editor):
    """Visitor.lead becomes one-to-one: when several visitors shared a
    lead, the earliest visitor keeps the link and the rest are detached
    (their sessions keep their own lead snapshot)."""
    ChatbotVisitor = apps.get_model("chatbot", "ChatbotVisitor")
    duplicate_lead_ids = (
        ChatbotVisitor.objects.exclude(lead_id=None)
        .values_list("lead_id", flat=True)
        .distinct()
    )
    for lead_id in duplicate_lead_ids:
        visitor_ids = list(
            ChatbotVisitor.objects.filter(lead_id=lead_id)
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        if len(visitor_ids) > 1:
            ChatbotVisitor.objects.filter(pk__in=visitor_ids[1:]).update(
                lead_id=None,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0020_chatbotvisitor_blocking'),
    ]

    operations = [
        migrations.RunPython(
            keep_one_visitor_per_lead,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='chatbotvisitor',
            name='lead',
            field=models.OneToOneField(
                blank=True,
                help_text=(
                    'Lead matched to this visitor once identified. New '
                    'sessions inherit this automatically.'
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='visitor',
                to='lead_capture.lead',
            ),
        ),
    ]
