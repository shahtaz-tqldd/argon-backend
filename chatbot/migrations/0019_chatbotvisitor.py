from django.db import migrations, models
import django.db.models.deletion
import uuid

import app.utils.validators


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0018_remove_chatbotconfig_active_features_and_more'),
        ('lead_capture', '0003_remove_leadcaptureconfig_intro_message'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatbotVisitor',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('visitor_id', models.CharField(help_text="Widget cookie/fingerprint identifier supplied by the visitor.", max_length=255)),
                ('ip_address', models.GenericIPAddressField(blank=True, help_text="Latest IP address the visitor was seen from.", null=True)),
                ('detected_location', models.CharField(blank=True, default='', help_text="Detected location, for example 'Dhaka, BD'.", max_length=255)),
                ('detected_country', models.CharField(blank=True, default='', help_text="Detected country name or ISO code.", max_length=64)),
                ('metadata', models.JSONField(blank=True, default=dict, help_text="Visitor-side context captured before/without a Lead — name, email, browser, locale, timezone, etc.", validators=[app.utils.validators.validate_json_object])),
                ('chatbot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='visitors', to='chatbot.chatbot')),
                ('lead', models.ForeignKey(blank=True, help_text='Lead matched to this visitor once identified. New sessions inherit this automatically.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='visitors', to='lead_capture.lead')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'constraints': [
                    models.UniqueConstraint(fields=['chatbot', 'visitor_id'], name='unique_visitor_per_chatbot'),
                    models.CheckConstraint(condition=models.Q(('visitor_id', ''), _negated=True), name='chatbot_visitor_id_not_blank'),
                ],
            },
        ),
        migrations.AddIndex(
            model_name='chatbotvisitor',
            index=models.Index(fields=['chatbot', 'lead'], name='chatbot_visitor_lead_idx'),
        ),
    ]
