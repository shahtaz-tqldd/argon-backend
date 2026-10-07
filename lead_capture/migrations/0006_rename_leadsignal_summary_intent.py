from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("lead_capture", "0005_leadaiinsight_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="leadsignal",
            old_name="summary",
            new_name="intent",
        ),
        migrations.AlterField(
            model_name="leadsignal",
            name="intent",
            field=models.CharField(
                help_text="One short sentence describing the visitor's intent behind the score.",
                max_length=240,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="leadsignal",
            name="lead_signal_summary_not_empty",
        ),
        migrations.AddConstraint(
            model_name="leadsignal",
            constraint=models.CheckConstraint(
                condition=~Q(intent=""),
                name="lead_signal_intent_not_empty",
            ),
        ),
    ]
