import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.services.verification import complete_email_verification
from workspace.models import WorkspaceInvitation, WorkspaceUser
from workspace.services.membership import join_workspace_from_invitation
from workspace.tasks import send_workspace_invitation_email

User = get_user_model()


class InvalidWorkspaceInvitation(ValueError):
    pass


def hash_invitation_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_valid_workspace_invitation(token):
    if not token:
        raise InvalidWorkspaceInvitation("Invitation token is required.")
    try:
        invitation = WorkspaceInvitation.objects.select_related("workspace").get(
            token_hash=hash_invitation_token(token),
        )
    except WorkspaceInvitation.DoesNotExist as exc:
        raise InvalidWorkspaceInvitation("Invalid invitation token.") from exc

    if invitation.is_accepted:
        raise InvalidWorkspaceInvitation("This invitation has already been used.")
    if invitation.is_expired:
        raise InvalidWorkspaceInvitation("This invitation has expired.")
    if not invitation.workspace.is_active:
        raise InvalidWorkspaceInvitation("This workspace is inactive.")
    return invitation


def _deliver_workspace_invitation(*, invitation, token):
    query = urlencode({"token": token, "email": invitation.email})
    invitation_link = (
        f"{settings.USER_FRONTEND_URL.rstrip('/')}"
        f"{settings.WORKSPACE_INVITATION_PATH}?{query}"
    )
    context = {
        "invitation": invitation,
        "inviter_name": invitation.created_by.name or invitation.created_by.email,
        "workspace_name": invitation.workspace.name,
        "invitation_link": invitation_link,
        "token": token,
        "expires_in_hours": settings.WORKSPACE_INVITATION_TTL_HOURS,
    }
    kwargs = {
        "recipient_email": invitation.email,
        "subject": f"Join {invitation.workspace.name} on Argon Chatbot",
        "message": render_to_string("emails/workspace_invitation.txt", context),
        "html_message": render_to_string("emails/workspace_invitation.html", context),
    }
    try:
        send_workspace_invitation_email.delay(**kwargs)
    except Exception:
        send_workspace_invitation_email(**kwargs)


@transaction.atomic
def issue_workspace_invitation(*, workspace, email, invited_by):
    if workspace.owner_id != invited_by.id:
        raise InvalidWorkspaceInvitation(
            "Only the workspace owner can invite members."
        )
    if not workspace.is_active:
        raise InvalidWorkspaceInvitation("Cannot invite members to an inactive workspace.")

    email = User.objects.normalize_email(email).strip().casefold()
    existing_user = User.objects.filter(email__iexact=email).first()
    if existing_user:
        if WorkspaceUser.objects.filter(
            workspace=workspace,
            user=existing_user,
            is_active=True,
        ).exists():
            raise InvalidWorkspaceInvitation(
                "This user is already a member of the workspace."
            )
        raise InvalidWorkspaceInvitation(
            "A user with this email already exists. Invite registration is only "
            "available for new users."
        )

    token = secrets.token_urlsafe(32)
    token_hash = hash_invitation_token(token)
    expires_at = timezone.now() + timedelta(
        hours=settings.WORKSPACE_INVITATION_TTL_HOURS
    )
    invitation = (
        WorkspaceInvitation.objects.select_for_update()
        .filter(workspace=workspace, email__iexact=email)
        .first()
    )
    if invitation is None:
        invitation = WorkspaceInvitation.objects.create(
            workspace=workspace,
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
        lambda: _deliver_workspace_invitation(invitation=invitation, token=token)
    )
    return invitation


@transaction.atomic
def accept_workspace_invitation(*, token, name, password):
    try:
        invitation = (
            WorkspaceInvitation.objects.select_for_update(of=("self",))
            .select_related("workspace", "created_by")
            .get(token_hash=hash_invitation_token(token))
        )
    except WorkspaceInvitation.DoesNotExist as exc:
        raise InvalidWorkspaceInvitation("Invalid invitation token.") from exc

    if invitation.accepted_at is not None:
        raise InvalidWorkspaceInvitation("This invitation has already been used.")
    if invitation.expires_at <= timezone.now():
        raise InvalidWorkspaceInvitation("This invitation has expired.")
    if not invitation.workspace.is_active:
        raise InvalidWorkspaceInvitation("This workspace is inactive.")
    if User.objects.filter(email__iexact=invitation.email).exists():
        raise InvalidWorkspaceInvitation(
            "An account with the invited email already exists."
        )

    try:
        user = User.objects.create_user(
            email=invitation.email,
            password=password,
            name=name,
            is_email_verified=True,
        )
        membership = join_workspace_from_invitation(
            workspace=invitation.workspace,
            user=user,
            invited_by=invitation.created_by,
        )
    except IntegrityError as exc:
        raise InvalidWorkspaceInvitation(
            "The invitation could not be accepted. Please request a new invitation."
        ) from exc

    invitation.accepted_at = timezone.now()
    invitation.updated_by = user
    invitation.save(update_fields=["accepted_at", "updated_by", "updated_at"])
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    complete_email_verification(user)
    return user, membership
