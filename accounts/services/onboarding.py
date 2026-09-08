from django.utils import timezone

from workspace.services.membership import ensure_personal_workspace


def provision_direct_signup(user):
    """Create a starter workspace only when the new account has no access path."""
    from chatbot.models import ChatbotInvitation, ChatbotUser
    from chatbot.utils.choices import ChatbotStatusTypes
    from workspace.models import Workspace, WorkspaceInvitation, WorkspaceUser

    if (
        Workspace.objects.filter(owner=user, is_active=True).exists()
        or WorkspaceUser.objects.filter(user=user, is_active=True).exists()
        or ChatbotUser.objects.filter(user=user, is_active=True).exists()
        or WorkspaceInvitation.objects.filter(
            email__iexact=user.email,
            accepted_at__isnull=True,
            expires_at__gt=timezone.now(),
            workspace__is_active=True,
        ).exists()
        or ChatbotInvitation.objects.filter(
            email__iexact=user.email,
            accepted_at__isnull=True,
            expires_at__gt=timezone.now(),
            chatbot__is_deleted=False,
            chatbot__workspace__is_active=True,
        )
        .exclude(
            chatbot__status__in=(
                ChatbotStatusTypes.DISABLED,
                ChatbotStatusTypes.DISABLED_BY_ADMIN,
            )
        )
        .exists()
    ):
        return None
    return ensure_personal_workspace(user)
