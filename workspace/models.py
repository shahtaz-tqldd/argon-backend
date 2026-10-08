from django.conf import settings
from django.db import models
from django.utils.text import slugify

from app.core.models import BaseModel
from app.utils.validators import validate_json_object


class Workspace(BaseModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(
        max_length=140,
        unique=True,
        blank=True,
        editable=False,
    )
    logo = models.URLField(blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_workspaces",
    )

    industry = models.CharField(
        max_length=100,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_json_object],
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["owner", "is_active"],
                name="workspace_owner_active_idx",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)[:120].strip("-") or "workspace"

            queryset = type(self).objects.all()
            if self.pk:
                queryset = queryset.exclude(pk=self.pk)

            candidate = base_slug
            suffix = 2

            while queryset.filter(slug=candidate).exists():
                suffix_text = f"-{suffix}"
                candidate = (
                    f"{base_slug[:140 - len(suffix_text)]}"
                    f"{suffix_text}"
                )
                suffix += 1

            self.slug = candidate

        super().save(*args, **kwargs)