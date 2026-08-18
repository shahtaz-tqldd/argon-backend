from django.contrib.auth import get_user_model
from django.db import transaction

from workspace.services.membership import (
    ensure_personal_workspace,
    join_workspace_from_invitation,
)

User = get_user_model()


def provision_direct_signup(user):
    """Provision the personal workspace required for a direct signup."""
    return ensure_personal_workspace(user)


@transaction.atomic
def create_user_from_workspace_invitation(
    *,
    workspace,
    email,
    password=None,
    invited_by=None,
    **extra_fields,
):
    """
    Create a user for a previously validated workspace invitation.

    This is intentionally separate from direct signup: it creates only the
    invited workspace membership and never creates a personal workspace.
    """
    user = User.objects.create_user(
        email=email,
        password=password,
        **extra_fields,
    )
    membership = join_workspace_from_invitation(
        workspace=workspace,
        user=user,
        invited_by=invited_by,
    )
    return user, membership
