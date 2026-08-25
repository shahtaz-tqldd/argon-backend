from django.apps import AppConfig


class BaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "app.base"
    verbose_name = "Argon Chatbot"

    def ready(self):
        from app.base import signals  # noqa: F401
