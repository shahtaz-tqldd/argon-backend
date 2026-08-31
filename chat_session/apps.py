from django.apps import AppConfig


class ChatSessionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chat_session"

    def ready(self):
        from chat_session import signals  # noqa: F401
