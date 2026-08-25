from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notification", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="notification_type",
            field=models.CharField(
                choices=[
                    ("general", "General"),
                    ("update", "Update"),
                    ("maintenance", "Maintenance"),
                    ("notify", "Notify"),
                    ("new_message", "New message"),
                    ("session_ended", "Session ended"),
                    ("session_started", "Session started"),
                    ("ai_notification", "AI notification"),
                    ("training_complete", "Training complete"),
                ],
                db_index=True,
                default="general",
                help_text="The event represented by the notification.",
                max_length=24,
            ),
        ),
    ]
