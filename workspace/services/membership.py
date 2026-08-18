from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from workspace.models import Workspace, WorkspaceRole, WorkspaceUser


def _personal_workspace_name(user):
    identity = (user.name or "").strip() or user.email.split("@", 1)[0]
    return f"{identity}'s Workspace"[:120]


@transaction.atomic
def ensure_personal_workspace(user):
    """Idempotently provision the default workspace used for a direct signup."""
    workspace = Workspace.objects.filter(owner=user).order_by("created_at").first()
    if workspace is None:
        workspace = Workspace.objects.create(
            owner=user,
            name=_personal_workspace_name(user),
            created_by=user,
        )
    membership, created = WorkspaceUser.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={
            "role": WorkspaceRole.ADMIN,
            "created_by": user,
        },
    )
    if not created and (
        membership.role != WorkspaceRole.ADMIN or not membership.is_active
    ):
        membership.role = WorkspaceRole.ADMIN
        membership.is_active = True
        membership.updated_by = user
        membership.save(
            update_fields=["role", "is_active", "updated_by", "updated_at"]
        )
    return workspace


def _require_workspace_admin(workspace, user):
    if not user.is_active or not WorkspaceUser.objects.filter(
        workspace=workspace,
        user=user,
        role=WorkspaceRole.ADMIN,
        is_active=True,
    ).exists():
        raise PermissionDenied("Only a workspace admin can add workspace users.")


@transaction.atomic
def add_workspace_user(
    *,
    workspace,
    user,
    added_by,
    role=WorkspaceRole.MEMBER,
):
    """Add or reactivate a workspace user after an admin-authorized action."""
    _require_workspace_admin(workspace, added_by)
    if not workspace.is_active:
        raise ValidationError("Cannot add users to an inactive workspace.")
    if not user.is_active:
        raise ValidationError("Cannot add an inactive user to a workspace.")

    role = WorkspaceRole(role)
    if workspace.owner_id == user.id and role != WorkspaceRole.ADMIN:
        raise ValidationError("A workspace owner must remain an admin.")
    membership, created = WorkspaceUser.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={
            "role": role,
            "created_by": added_by,
        },
    )
    if not created:
        if not (
            membership.role == WorkspaceRole.ADMIN
            and role == WorkspaceRole.MEMBER
        ):
            membership.role = role
        membership.is_active = True
        membership.updated_by = added_by
        membership.full_clean()
        membership.save(
            update_fields=["role", "is_active", "updated_by", "updated_at"]
        )
    return membership


@transaction.atomic
def join_workspace_from_invitation(*, workspace, user, invited_by=None):
    """
    Join a user after an invitation has been validated by the caller.

    This path intentionally never provisions a personal workspace.
    """
    if not workspace.is_active:
        raise ValidationError("Cannot join an inactive workspace.")
    if not user.is_active:
        raise ValidationError("An inactive user cannot join a workspace.")

    membership, created = WorkspaceUser.objects.get_or_create(
        workspace=workspace,
        user=user,
        defaults={
            "role": WorkspaceRole.MEMBER,
            "created_by": invited_by,
        },
    )
    if not created and not membership.is_active:
        membership.is_active = True
        membership.updated_by = invited_by
        membership.save(
            update_fields=["is_active", "updated_by", "updated_at"]
        )
    return membership
