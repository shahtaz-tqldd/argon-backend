import app.utils.validators
import chatbot.utils.validation
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('workspace', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Chatbot',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True)),
                ('slug', models.SlugField(blank=True, editable=False, max_length=140, unique=True)),
                ('instructions', models.TextField(blank=True)),
                ('logo', models.URLField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('disabled', 'Disabled'), ('disabled_by_admin', 'Disabled by Admin')], default='draft', max_length=30)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chatbots', to='workspace.workspace')),
            ],
            options={
                'ordering': ['workspace__name', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ChatbotAllowedOrigin',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('origin', models.CharField(help_text='Allowed widget origin, for example https://www.example.com.', max_length=300)),
                ('is_active', models.BooleanField(default=True)),
                ('chatbot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allowed_origins', to='chatbot.chatbot')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['origin'],
            },
        ),
        migrations.CreateModel(
            name='ChatbotInvitation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('email', models.EmailField(max_length=254)),
                ('token_hash', models.CharField(editable=False, max_length=64, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('accepted_at', models.DateTimeField(blank=True, null=True)),
                ('chatbot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invitations', to='chatbot.chatbot')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ChatbotSettings',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('welcome_message', models.TextField(blank=True)),
                ('fallback_message', models.TextField(blank=True)),
                ('language', models.CharField(default='en', max_length=20)),
                ('timezone', models.CharField(default='UTC', help_text='IANA timezone for localized activity, for example Asia/Dhaka.', max_length=64, validators=[app.utils.validators.validate_timezone_name])),
                ('ai_enabled', models.BooleanField(default=True)),
                ('knowledge_base_enabled', models.BooleanField(default=True, help_text="Allow answers grounded in the chatbot's knowledge bases.")),
                ('appointment_booking_enabled', models.BooleanField(default=False, help_text='Allow customers to book appointments or meetings.')),
                ('quotation_generation_enabled', models.BooleanField(default=False, help_text='Allow the chatbot to prepare pricing and quotations.')),
                ('lead_capture_enabled', models.BooleanField(default=False, help_text='Allow the chatbot to capture and qualify prospective leads.')),
                ('human_handoff_enabled', models.BooleanField(default=False, help_text='Allow the chatbot to handoff to the human assistant.')),
                ('order_taking_enabled', models.BooleanField(default=False, help_text='Allow the chatbot to collect and submit customer orders.')),
                ('widget_public_key', models.CharField(default=chatbot.utils.validation.generate_widget_public_key, editable=False, help_text='Public key embedded in the generated widget script.', max_length=64, unique=True)),
                ('widget_enabled', models.BooleanField(default=True)),
                ('widget_settings', models.JSONField(blank=True, default=dict, help_text='Widget presentation settings such as colors, text, position, and launcher appearance.', validators=[chatbot.utils.validation.validate_widget_settings])),
                ('other_settings', models.JSONField(blank=True, default=dict, help_text='Miscellaneous settings to operate chatbot', validators=[chatbot.utils.validation.validate_other_settings])),
                ('chatbot', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='settings', to='chatbot.chatbot')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ChatbotUser',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('admin', 'Admin'), ('member', 'Member')], default='member', max_length=12)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('chatbot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='chatbot.chatbot')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chatbot_memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['chatbot__name', 'user__email'],
            },
        ),
        migrations.AddIndex(
            model_name='chatbot',
            index=models.Index(fields=['workspace', 'is_deleted'], name='chatbot_workspace_active_idx'),
        ),
        migrations.AddIndex(
            model_name='chatbot',
            index=models.Index(fields=['workspace', 'status'], name='chatbot_workspace_status_idx'),
        ),
        migrations.AddConstraint(
            model_name='chatbot',
            constraint=models.UniqueConstraint(fields=('workspace', 'name'), name='unique_chatbot_name_per_workspace'),
        ),
        migrations.AddIndex(
            model_name='chatbotallowedorigin',
            index=models.Index(fields=['chatbot', 'is_active'], name='chatbot_origin_active_idx'),
        ),
        migrations.AddConstraint(
            model_name='chatbotallowedorigin',
            constraint=models.UniqueConstraint(fields=('chatbot', 'origin'), name='unique_origin_per_chatbot'),
        ),
        migrations.AddIndex(
            model_name='chatbotinvitation',
            index=models.Index(fields=['chatbot', 'expires_at'], name='chatbot_invite_expiry_idx'),
        ),
        migrations.AddConstraint(
            model_name='chatbotinvitation',
            constraint=models.UniqueConstraint(fields=('chatbot', 'email'), name='unique_chatbot_invitation_email'),
        ),
        migrations.AddIndex(
            model_name='chatbotuser',
            index=models.Index(fields=['user', 'is_active'], name='chatbot_user_active_idx'),
        ),
        migrations.AddIndex(
            model_name='chatbotuser',
            index=models.Index(fields=['chatbot', 'role', 'is_active'], name='chatbot_role_active_idx'),
        ),
        migrations.AddConstraint(
            model_name='chatbotuser',
            constraint=models.UniqueConstraint(fields=('chatbot', 'user'), name='unique_user_per_chatbot'),
        ),
    ]
