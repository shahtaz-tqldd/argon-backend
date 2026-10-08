from django.db import transaction

from workspace.models import Workspace


@transaction.atomic
def ensure_personal_workspace(user):
    """Return the user's personal workspace, creating it on first access.

    Workspaces have a single owner and no membership rows; each user gets
    one personal workspace.
    """
    workspace = (
        Workspace.objects.select_for_update()
        .filter(owner=user)
        .first()
    )
    if workspace is None:
        workspace = Workspace.objects.create(
            name=f"{user.name or user.email.split('@')[0]}'s Workspace",
            owner=user,
            created_by=user,
        )
    return workspace
