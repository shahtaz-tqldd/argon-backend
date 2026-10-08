from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0021_visitor_lead_one_to_one"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="chatbot",
            name="unique_chatbot_name_per_workspace",
        ),
    ]
