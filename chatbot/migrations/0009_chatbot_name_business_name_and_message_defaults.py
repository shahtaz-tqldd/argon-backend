from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0008_chatbot_escalation_rule_chatbot_never_answer"),
    ]

    operations = [
        migrations.RenameField(
            model_name="chatbot",
            old_name="name",
            new_name="chatbot_name",
        ),
        migrations.AlterModelOptions(
            name="chatbot",
            options={
                "ordering": ["workspace__name", "chatbot_name"],
            },
        ),
        migrations.AlterModelOptions(
            name="chatbotuser",
            options={
                "ordering": ["chatbot__chatbot_name", "user__email"],
            },
        ),
        migrations.RemoveConstraint(
            model_name="chatbot",
            name="unique_chatbot_name_per_workspace",
        ),
        migrations.AddConstraint(
            model_name="chatbot",
            constraint=models.UniqueConstraint(
                fields=("workspace", "chatbot_name"),
                name="unique_chatbot_name_per_workspace",
            ),
        ),
        migrations.AddField(
            model_name="chatbot",
            name="business_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AlterField(
            model_name="chatbot",
            name="welcome_message",
            field=models.TextField(
                blank=True,
                default=(
                    "Hey, I am {chatbot_name} Chatbot Assistant, I am here to "
                    "answer anything you want to know."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="chatbot",
            name="fallback_message",
            field=models.TextField(
                blank=True,
                default=(
                    "Sorry, I couldn't find anything to my knowledge to answer "
                    "this question, should I connect with you one of our human "
                    "assistant?"
                ),
            ),
        ),
        migrations.AlterField(
            model_name="chatbot",
            name="escalation_rule",
            field=models.TextField(
                blank=True,
                default=(
                    "Hand off to human agent, when you don't find any answer, "
                    "asking about payment or collaboration."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="chatbot",
            name="never_answer",
            field=models.TextField(
                blank=True,
                default=(
                    "Never answer about payment, outside scope and all."
                ),
            ),
        ),
    ]
