import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Workspace',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('slug', models.SlugField(blank=True, editable=False, max_length=140, unique=True)),
                ('logo', models.URLField(blank=True)),
                ('industry', models.CharField(blank=True, max_length=100)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='owned_workspaces', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='WorkspaceInvitation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('email', models.EmailField(max_length=254)),
                ('token_hash', models.CharField(editable=False, max_length=64, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('accepted_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invitations', to='workspace.workspace')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='WorkspaceUser',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('admin', 'Admin'), ('member', 'Member')], default='member', max_length=12)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_memberships', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='workspace.workspace')),
            ],
            options={
                'ordering': ['workspace__name', 'user__email'],
            },
        ),
        migrations.AddIndex(
            model_name='workspace',
            index=models.Index(fields=['owner', 'is_active'], name='workspace_owner_active_idx'),
        ),
        migrations.AddIndex(
            model_name='workspaceinvitation',
            index=models.Index(fields=['workspace', 'expires_at'], name='workspace_invite_expiry_idx'),
        ),
        migrations.AddConstraint(
            model_name='workspaceinvitation',
            constraint=models.UniqueConstraint(fields=('workspace', 'email'), name='unique_workspace_invitation_email'),
        ),
        migrations.AddIndex(
            model_name='workspaceuser',
            index=models.Index(fields=['user', 'is_active'], name='workspace_user_active_idx'),
        ),
        migrations.AddIndex(
            model_name='workspaceuser',
            index=models.Index(fields=['workspace', 'role', 'is_active'], name='workspace_role_active_idx'),
        ),
        migrations.AddConstraint(
            model_name='workspaceuser',
            constraint=models.UniqueConstraint(fields=('workspace', 'user'), name='unique_user_per_workspace'),
        ),
    ]
