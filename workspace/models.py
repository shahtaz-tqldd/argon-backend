from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from app.base.models import BaseModel


class WorkspaceRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class Workspace(BaseModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True, editable=False)
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
    is_active = models.BooleanField(default=True, db_index=True)

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
            from workspace.services.slugs import generate_workspace_slug

            self.slug = generate_workspace_slug(self.name, workspace_id=self.pk)
        super().save(*args, **kwargs)


class WorkspaceUser(BaseModel):
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspace_memberships",
    )
    role = models.CharField(
        max_length=12,
        choices=WorkspaceRole.choices,
        default=WorkspaceRole.MEMBER,
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["workspace__name", "user__email"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"],
                name="unique_user_per_workspace",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "is_active"],
                name="workspace_user_active_idx",
            ),
            models.Index(
                fields=["workspace", "role", "is_active"],
                name="workspace_role_active_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.workspace_id
            and self.user_id
            and self.workspace.owner_id == self.user_id
            and self.role != WorkspaceRole.ADMIN
        ):
            raise ValidationError("A workspace owner must be an admin.")

    def __str__(self):
        return f"{self.user} in {self.workspace} ({self.get_role_display()})"


class WorkspaceInvitation(BaseModel):
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "email"],
                name="unique_workspace_invitation_email",
            ),
        ]
        indexes = [
            models.Index(
                fields=["workspace", "expires_at"],
                name="workspace_invite_expiry_idx",
            ),
        ]

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def is_accepted(self):
        return self.accepted_at is not None

    def save(self, *args, **kwargs):
        self.email = self.email.strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invitation for {self.email} to {self.workspace}"
