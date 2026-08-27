import chatbot.utils.validation
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "chatbot",
            "0009_chatbot_name_business_name_and_message_defaults",
        ),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatbotwidgetsettings",
            name="secondary_color",
            field=models.CharField(
                default="#fafafa",
                max_length=9,
                validators=[chatbot.utils.validation.validate_hex_color],
            ),
        ),
    ]
