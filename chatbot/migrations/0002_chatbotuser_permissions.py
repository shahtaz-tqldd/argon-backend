import chatbot.utils.permissions
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatbotuser",
            name="permissions",
            field=models.JSONField(
                blank=True,
                default=(
                    chatbot.utils.permissions.default_chatbot_user_permissions
                ),
                help_text=(
                    "Permission codes explicitly granted to this chatbot member."
                ),
            ),
        ),
    ]
