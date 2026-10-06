import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models
from django.db.models import Q
import uuid


def carry_lead_score_into_avg(apps, schema_editor):
    """Preserve the legacy single score as the lead's initial avg_score."""
    Lead = apps.get_model("lead_capture", "Lead")
    leads = Lead.objects.exclude(lead_score=None)
    for lead in leads.iterator(chunk_size=1000):
        Lead.objects.filter(pk=lead.pk).update(
            avg_score=Decimal(lead.lead_score).quantize(Decimal("0.01")),
        )


class Migration(migrations.Migration):

    dependencies = [
        ('lead_capture', '0003_remove_leadcaptureconfig_intro_message'),
        ('chat_session', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='avg_score',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Average score across this lead's qualification signals.",
                max_digits=5,
                null=True,
            ),
        ),
        migrations.RunPython(
            carry_lead_score_into_avg,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name='LeadSignal',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('score', models.PositiveSmallIntegerField(help_text='Qualification score from 0 through 100.')),
                ('summary', models.CharField(help_text='One short sentence explaining the evidence for the score.', max_length=240)),
                ('lead', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='signals', to='lead_capture.lead')),
                ('message', models.ForeignKey(blank=True, help_text='The AI reply the score was recorded with, if any.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='lead_signals', to='chat_session.chatmessage')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'constraints': [
                    models.UniqueConstraint(condition=Q(message__isnull=False), fields=('message',), name='unique_lead_signal_per_message'),
                    models.CheckConstraint(condition=~Q(summary=''), name='lead_signal_summary_not_empty'),
                    models.CheckConstraint(condition=Q(score__gte=0, score__lte=100), name='lead_signal_score_between_0_and_100'),
                ],
            },
        ),
        migrations.AddIndex(
            model_name='leadsignal',
            index=models.Index(fields=['lead', '-created_at'], name='lead_signal_time_idx'),
        ),
        migrations.RemoveField(
            model_name='lead',
            name='initial_ip_address',
        ),
        migrations.RemoveField(
            model_name='lead',
            name='last_ip_address',
        ),
        migrations.RemoveField(
            model_name='lead',
            name='detected_country_code',
        ),
        migrations.RemoveField(
            model_name='lead',
            name='detected_city',
        ),
        migrations.RemoveField(
            model_name='lead',
            name='lead_score',
        ),
        migrations.AddConstraint(
            model_name='lead',
            constraint=models.CheckConstraint(
                condition=Q(avg_score__isnull=True) | Q(avg_score__gte=0, avg_score__lte=100),
                name='lead_avg_score_between_0_and_100',
            ),
        ),
    ]
