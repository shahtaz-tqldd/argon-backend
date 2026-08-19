import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('chatbot', '0001_initial'),
        ('workspace', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('recipient_type', models.CharField(choices=[('global', 'Global'), ('workspace', 'Workspace'), ('chatbot', 'Chatbot'), ('user', 'User'), ('chat_session', 'Chat session')], db_index=True, help_text='The audience addressed by this notification.', max_length=20)),
                ('target_id', models.UUIDField(blank=True, db_index=True, help_text='Chat-session UUID until the chat-session model is available.', null=True)),
                ('notification_type', models.CharField(choices=[('general', 'General'), ('update', 'Update'), ('maintenance', 'Maintenance'), ('notify', 'Notify'), ('new_message', 'New message'), ('session_ended', 'Session ended'), ('session_started', 'Session started'), ('ai_notification', 'AI notification')], db_index=True, default='general', help_text='The event represented by the notification.', max_length=24)),
                ('title', models.CharField(max_length=180)),
                ('message', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('chatbot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to='chatbot.chatbot')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('recipient', models.ForeignKey(blank=True, help_text='Set only when recipient_type is user.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
                ('workspace', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to='workspace.workspace')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='NotificationRead',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('read_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_created_records', to=settings.AUTH_USER_MODEL)),
                ('notification', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='read_receipts', to='notification.notification')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(app_label)s_%(class)s_updated_records', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_reads', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-read_at'],
            },
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['recipient_type', 'recipient', '-created_at'], name='notif_user_recipient_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['recipient_type', 'workspace', '-created_at'], name='notif_workspace_scope_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['recipient_type', 'chatbot', '-created_at'], name='notif_chatbot_scope_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['recipient_type', 'target_id', '-created_at'], name='notif_session_scope_idx'),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(fields=['notification_type', '-created_at'], name='notif_event_type_idx'),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('chatbot__isnull', True), ('recipient__isnull', True), ('recipient_type', 'global'), ('target_id__isnull', True), ('workspace__isnull', True)), models.Q(('chatbot__isnull', True), ('recipient__isnull', False), ('recipient_type', 'user'), ('target_id__isnull', True), ('workspace__isnull', True)), models.Q(('chatbot__isnull', True), ('recipient__isnull', True), ('recipient_type', 'workspace'), ('target_id__isnull', True), ('workspace__isnull', False)), models.Q(('chatbot__isnull', False), ('recipient__isnull', True), ('recipient_type', 'chatbot'), ('target_id__isnull', True), ('workspace__isnull', True)), models.Q(('chatbot__isnull', True), ('recipient__isnull', True), ('recipient_type', 'chat_session'), ('target_id__isnull', False), ('workspace__isnull', True)), _connector='OR'), name='notification_recipient_shape_is_valid'),
        ),
        migrations.AddIndex(
            model_name='notificationread',
            index=models.Index(fields=['user', 'read_at'], name='notificatio_user_id_93189a_idx'),
        ),
        migrations.AddConstraint(
            model_name='notificationread',
            constraint=models.UniqueConstraint(fields=('notification', 'user'), name='unique_notification_read_per_user'),
        ),
    ]
