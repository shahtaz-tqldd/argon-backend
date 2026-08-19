import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from chatbot.models import ChatbotInvitation, ChatbotUser
from chatbot.tasks import send_chatbot_invitation_email
from chatbot.utils.choices import ChatbotRoleTypes
from workspace.models import WorkspaceRole, WorkspaceUser

User = get_user_model()


class InvalidChatbotInvitation(ValueError):
    pass


def hash_invitation_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_chatbot_manager(chatbot, user):
    if not user or not user.is_active:
        return False
    workspace_membership = WorkspaceUser.objects.filter(
        workspace=chatbot.workspace,
        user=user,
        is_active=True,
    )
    if not workspace_membership.exists():
        return False
    return (
        workspace_membership.filter(role=WorkspaceRole.ADMIN).exists()
        or ChatbotUser.objects.filter(
            chatbot=chatbot,
            user=user,
            role=ChatbotRoleTypes.ADMIN,
            is_active=True,
        ).exists()
    )


def get_valid_chatbot_invitation(token):
    if not token:
        raise InvalidChatbotInvitation("Invitation token is required.")
    try:
        invitation = ChatbotInvitation.objects.select_related(
            "chatbot",
            "chatbot__workspace",
        ).get(token_hash=hash_invitation_token(token))
    except ChatbotInvitation.DoesNotExist as exc:
        raise InvalidChatbotInvitation("Invalid invitation token.") from exc

    if invitation.is_accepted:
        raise InvalidChatbotInvitation("This invitation has already been used.")
    if invitation.is_expired:
        raise InvalidChatbotInvitation("This invitation has expired.")
    if (
        not invitation.chatbot.is_active
        or invitation.chatbot.is_deleted
        or not invitation.chatbot.workspace.is_active
    ):
        raise InvalidChatbotInvitation("This chatbot is inactive.")
    return invitation


def _deliver_chatbot_invitation(*, invitation, token):
    query = urlencode({"token": token})
    invitation_link = (
        f"{settings.USER_FRONTEND_URL.rstrip('/')}"
        f"{settings.CHATBOT_INVITATION_PATH}?{query}"
    )
    context = {
        "invitation": invitation,
        "inviter_name": (
            invitation.created_by.name or invitation.created_by.email
        ),
        "chatbot_name": invitation.chatbot.name,
        "workspace_name": invitation.chatbot.workspace.name,
        "invitation_link": invitation_link,
        "token": token,
        "expires_in_hours": settings.CHATBOT_INVITATION_TTL_HOURS,
    }
    kwargs = {
        "recipient_email": invitation.email,
        "subject": f"Join {invitation.chatbot.name} on Argon Chatbot",
        "message": render_to_string("emails/chatbot_invitation.txt", context),
        "html_message": render_to_string(
            "emails/chatbot_invitation.html",
            context,
        ),
    }
    try:
        send_chatbot_invitation_email.delay(**kwargs)
    except Exception:
        send_chatbot_invitation_email(**kwargs)


@transaction.atomic
def issue_chatbot_invitation(*, chatbot, email, invited_by):
    if not _is_chatbot_manager(chatbot, invited_by):
        raise InvalidChatbotInvitation(
            "Only a workspace admin or chatbot admin can invite chatbot members."
        )
    if (
        not chatbot.is_active
        or chatbot.is_deleted
        or not chatbot.workspace.is_active
    ):
        raise InvalidChatbotInvitation("Cannot invite members to an inactive chatbot.")

    email = User.objects.normalize_email(email).strip().casefold()
    invited_user = User.objects.filter(email__iexact=email, is_active=True).first()
    if invited_user is None or not WorkspaceUser.objects.filter(
        workspace=chatbot.workspace,
        user=invited_user,
        is_active=True,
    ).exists():
        raise InvalidChatbotInvitation(
            "The invited user must be an active member of the chatbot workspace."
        )
    if ChatbotUser.objects.filter(
        chatbot=chatbot,
        user=invited_user,
        is_active=True,
    ).exists():
        raise InvalidChatbotInvitation(
            "This user is already a member of the chatbot."
        )

    token = secrets.token_urlsafe(32)
    token_hash = hash_invitation_token(token)
    expires_at = timezone.now() + timedelta(
        hours=settings.CHATBOT_INVITATION_TTL_HOURS
    )
    invitation = (
        ChatbotInvitation.objects.select_for_update()
        .filter(chatbot=chatbot, email__iexact=email)
        .first()
    )
    if invitation is None:
        invitation = ChatbotInvitation.objects.create(
            chatbot=chatbot,
            email=email,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=invited_by,
        )
    else:
        invitation.email = email
        invitation.token_hash = token_hash
        invitation.expires_at = expires_at
        invitation.accepted_at = None
        invitation.created_by = invited_by
        invitation.updated_by = invited_by
        invitation.save(
            update_fields=[
                "email",
                "token_hash",
                "expires_at",
                "accepted_at",
                "created_by",
                "updated_by",
                "updated_at",
            ]
        )

    transaction.on_commit(
        lambda: _deliver_chatbot_invitation(invitation=invitation, token=token)
    )
    return invitation


@transaction.atomic
def accept_chatbot_invitation(*, token):
    try:
        invitation = (
            ChatbotInvitation.objects.select_for_update()
            .select_related("chatbot", "chatbot__workspace")
            .get(token_hash=hash_invitation_token(token))
        )
    except ChatbotInvitation.DoesNotExist as exc:
        raise InvalidChatbotInvitation("Invalid invitation token.") from exc

    if invitation.accepted_at is not None:
        raise InvalidChatbotInvitation("This invitation has already been used.")
    if invitation.expires_at <= timezone.now():
        raise InvalidChatbotInvitation("This invitation has expired.")
    if (
        not invitation.chatbot.is_active
        or invitation.chatbot.is_deleted
        or not invitation.chatbot.workspace.is_active
    ):
        raise InvalidChatbotInvitation("This chatbot is inactive.")

    user = User.objects.filter(
        email__iexact=invitation.email,
        is_active=True,
    ).first()
    if user is None or not WorkspaceUser.objects.filter(
        workspace=invitation.chatbot.workspace,
        user=user,
        is_active=True,
    ).exists():
        raise InvalidChatbotInvitation(
            "The invited user is no longer an active workspace member."
        )

    try:
        membership, created = ChatbotUser.objects.get_or_create(
            chatbot=invitation.chatbot,
            user=user,
            defaults={"role": ChatbotRoleTypes.MEMBER},
        )
        if not created and not membership.is_active:
            membership.is_active = True
            membership.save(update_fields=["is_active", "updated_at"])
    except IntegrityError as exc:
        raise InvalidChatbotInvitation(
            "The invitation could not be accepted. Please request a new invitation."
        ) from exc

    invitation.accepted_at = timezone.now()
    invitation.updated_by = user
    invitation.save(update_fields=["accepted_at", "updated_by", "updated_at"])
    return membership
