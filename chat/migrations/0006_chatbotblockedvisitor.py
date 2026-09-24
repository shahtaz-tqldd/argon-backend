import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat_session', '0005_alter_takeover_release_reason'),
        ('chatbot', '0014_chatbotactivitylog'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatbotBlockedVisitor',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('visitor_id', models.CharField(max_length=255)),
                ('blocked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='blocked_chat_visitors', to='chatbot.chatbotuser')),
                ('chatbot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blocked_visitors', to='chatbot.chatbot')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'constraints': [models.UniqueConstraint(fields=('chatbot', 'visitor_id'), name='unique_blocked_visitor_per_chatbot'), models.CheckConstraint(condition=models.Q(('visitor_id', ''), _negated=True), name='blocked_visitor_id_not_blank')],
            },
        ),
    ]
