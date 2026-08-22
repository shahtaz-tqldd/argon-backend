import chatbot.utils.permissions
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0002_chatbotuser_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatbotinvitation",
            name="permissions",
            field=models.JSONField(
                blank=True,
                default=(
                    chatbot.utils.permissions.default_chatbot_user_permissions
                ),
                help_text="Permissions to grant when this invitation is accepted.",
            ),
        ),
    ]
