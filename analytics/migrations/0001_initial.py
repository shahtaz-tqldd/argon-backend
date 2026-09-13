import django.db.models.deletion
import uuid

import analytics.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("chatbot", "0013_chatbotcapacity"),
        ("chat_session", "0002_chatsessiontransfer_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AIUsage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("usage_type", models.CharField(choices=[("chat", "Chat")], db_index=True, max_length=30)),
                ("cost", models.DecimalField(decimal_places=8, default=0, max_digits=12)),
                ("tokens", models.PositiveIntegerField(default=0)),
                ("input_tokens", models.PositiveIntegerField(default=0)),
                ("output_tokens", models.PositiveIntegerField(default=0)),
                ("thinking_tokens", models.PositiveIntegerField(default=0)),
                ("cached_input_tokens", models.PositiveIntegerField(default=0)),
                ("model", models.CharField(blank=True, db_index=True, default="", max_length=120)),
                ("metadata", models.JSONField(blank=True, default=dict, validators=[analytics.validators.validate_ai_usage_metadata])),
                ("chat_message", models.OneToOneField(blank=True, help_text="The generated message, when this usage produced one.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ai_usage", to="chat_session.chatmessage")),
                ("chat_session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ai_usages", to="chat_session.chatsession")),
                ("chatbot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ai_usages", to="chatbot.chatbot")),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["chatbot", "usage_type", "-created_at"], name="ai_usage_bot_type_idx"),
                    models.Index(fields=["chat_session", "-created_at"], name="ai_usage_session_idx"),
                ],
            },
        ),
    ]
