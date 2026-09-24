from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0014_chatbotactivitylog"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatbotcapacity",
            name="current_test_ai_message_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="chatbotcapacity",
            name="test_ai_message_limit",
            field=models.PositiveIntegerField(
                default=100,
                help_text=(
                    "Free AI replies reserved for chatbot test sessions before "
                    "subscription messages are used."
                ),
            ),
        ),
    ]
