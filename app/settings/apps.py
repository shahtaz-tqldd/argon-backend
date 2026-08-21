DJANGO_BASE_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django.contrib.postgres",
    "rest_framework",
    "corsheaders",
    "django_celery_results",
    "channels",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
]


MODULER_APPS = [
    "app.base.apps.BaseConfig",
    "accounts.apps.AccountsConfig",
    "workspace.apps.WorkspaceConfig",
    "chatbot.apps.ChatbotConfig",
    "subscription.apps.SubscriptionConfig",
    "promotion.apps.PromotionConfig",
    "notification.apps.NotificationConfig",
    # "vector_store.apps.VectorStoreConfig",
    # "analytics.apps.AnalyticsConfig",
]

INSTALLED_APPS = DJANGO_BASE_APPS + THIRD_PARTY_APPS + MODULER_APPS
