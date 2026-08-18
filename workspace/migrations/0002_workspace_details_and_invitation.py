import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils.text import slugify


def populate_workspace_slugs(apps, schema_editor):
    Workspace = apps.get_model("workspace", "Workspace")
    used_slugs = set()
    for workspace in Workspace.objects.order_by("created_at", "pk").iterator():
        base_slug = slugify(workspace.name)[:120].strip("-") or "workspace"
        candidate = base_slug
        suffix = 2
        while candidate in used_slugs:
            suffix_text = f"-{suffix}"
            candidate = f"{base_slug[: 140 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        workspace.slug = candidate
        workspace.save(update_fields=["slug"])
        used_slugs.add(candidate)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("workspace", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="workspace",
            name="unique_personal_workspace_per_owner",
        ),
        migrations.RemoveField(
            model_name="workspace",
            name="description",
        ),
        migrations.RemoveField(
            model_name="workspace",
            name="is_personal",
        ),
        migrations.AddField(
            model_name="workspace",
            name="industry",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="workspace",
            name="logo",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="workspace",
            name="slug",
            # Use a plain CharField for the nullable backfill stage. On
            # PostgreSQL, SlugField implicitly creates a pattern-ops `_like`
            # index; altering that field to unique below would try to create
            # the same index a second time.
            field=models.CharField(blank=True, max_length=140, null=True),
        ),
        migrations.RunPython(populate_workspace_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="workspace",
            name="slug",
            field=models.SlugField(blank=True, editable=False, max_length=140, unique=True),
        ),
        migrations.CreateModel(
            name="WorkspaceInvitation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254)),
                (
                    "token_hash",
                    models.CharField(editable=False, max_length=64, unique=True),
                ),
                ("expires_at", models.DateTimeField()),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invitations",
                        to="workspace.workspace",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="workspaceinvitation",
            constraint=models.UniqueConstraint(
                fields=("workspace", "email"),
                name="unique_workspace_invitation_email",
            ),
        ),
        migrations.AddIndex(
            model_name="workspaceinvitation",
            index=models.Index(
                fields=["workspace", "expires_at"],
                name="workspace_invite_expiry_idx",
            ),
        ),
    ]
