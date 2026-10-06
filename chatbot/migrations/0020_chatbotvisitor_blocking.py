import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0019_chatbotvisitor'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatbotvisitor',
            name='is_blocked',
            field=models.BooleanField(default=False, help_text='Blocked visitors are rejected from all chatbot interactions.'),
        ),
        migrations.AddField(
            model_name='chatbotvisitor',
            name='blocked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='chatbotvisitor',
            name='blocked_by',
            field=models.ForeignKey(blank=True, help_text='The agent who blocked this visitor, if any.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='blocked_visitors', to='chatbot.chatbotuser'),
        ),
        migrations.AddConstraint(
            model_name='chatbotvisitor',
            constraint=models.CheckConstraint(
                condition=(
                    Q(is_blocked=False, blocked_at__isnull=True, blocked_by__isnull=True)
                    | Q(is_blocked=True, blocked_at__isnull=False)
                ),
                name='chatbot_visitor_block_fields_consistent',
            ),
        ),
        migrations.AddIndex(
            model_name='chatbotvisitor',
            index=models.Index(fields=['chatbot', 'is_blocked'], name='chatbot_visitor_blocked_idx'),
        ),
    ]
