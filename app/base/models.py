from uuid import uuid4
from django.db import models
from django.conf import settings


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_created_records",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_updated_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class BaseMinModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    image_url = models.URLField()
    caption = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_created_records",
    )

    class Meta:
        abstract = True
        ordering = ["sort_order"]


class ArgonChatbotConfig(models.Model):
    # Branding
    title = models.CharField(max_length=200, default="Argon Chatbot")
    logo = models.URLField(blank=True)
    favicon = models.URLField(blank=True)
    support_email = models.EmailField(blank=True)

    # Legal / Policy
    privacy_policy = models.TextField(blank=True)
    terms_of_service = models.TextField(blank=True)
    data_deletion_policy = models.TextField(blank=True)
    cookie_policy = models.TextField(blank=True)

    # Feature flags
    is_vectorize_enabled = models.BooleanField(default=True)
    maintenance_mode = models.BooleanField(default=False)

    # Announcement
    notify_banner_enabled = models.BooleanField(default=False)
    notify_banner_text = models.CharField(max_length=500, blank=True)
    notify_banner_url = models.URLField(blank=True)

    # Platform limits / defaults
    default_free_credits = models.PositiveIntegerField(default=100)
    monthly_free_credits = models.PositiveIntegerField(default=20)

    # SEO / public metadata
    meta_title = models.CharField(max_length=200, blank=True)
    meta_description = models.TextField(blank=True)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_updated_records",
    )

    class Meta:
        verbose_name = "Argon Chatbot Configuration"
        verbose_name_plural = "Argon Chatbot Configuration"

    def __str__(self):
        return self.title or "Argon Chatbot Configuration"

    def save(self, *args, **kwargs):
        # Keep this model as a singleton
        if not self.pk and ArgonChatbotConfig.objects.exists():
            raise ValueError("Only one ArgonChatbotConfig instance is allowed.")

        super().save(*args, **kwargs)
