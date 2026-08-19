import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ArgonChatbotConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(default='Argon Chatbot', max_length=200)),
                ('logo', models.URLField(blank=True)),
                ('favicon', models.URLField(blank=True)),
                ('support_email', models.EmailField(blank=True, max_length=254)),
                ('privacy_policy', models.TextField(blank=True)),
                ('terms_of_service', models.TextField(blank=True)),
                ('data_deletion_policy', models.TextField(blank=True)),
                ('cookie_policy', models.TextField(blank=True)),
                ('is_vectorize_enabled', models.BooleanField(default=True)),
                ('maintenance_mode', models.BooleanField(default=False)),
                ('notify_banner_enabled', models.BooleanField(default=False)),
                ('notify_banner_text', models.CharField(blank=True, max_length=500)),
                ('notify_banner_url', models.URLField(blank=True)),
                ('default_free_credits', models.PositiveIntegerField(default=100)),
                ('monthly_free_credits', models.PositiveIntegerField(default=20)),
                ('meta_title', models.CharField(blank=True, max_length=200)),
                ('meta_description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Argon Chatbot Configuration',
                'verbose_name_plural': 'Argon Chatbot Configuration',
            },
        ),
    ]
