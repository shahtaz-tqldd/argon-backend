from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chatbot", "0016_chatbotactivitylog_module"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="ChatbotCapacity",
            new_name="ChatbotConfig",
        ),
        migrations.AlterModelOptions(
            name="chatbotconfig",
            options={
                "verbose_name": "Chatbot config",
                "verbose_name_plural": "Chatbot configs",
            },
        ),
    ]
