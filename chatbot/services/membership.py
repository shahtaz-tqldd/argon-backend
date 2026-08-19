from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from workspace.models import WorkspaceUser


def _require_active_workspace_user(workspace, user):
    if not user.is_active:
        raise PermissionDenied("Inactive users cannot access a workspace.")
    membership = WorkspaceUser.objects.filter(
        workspace=workspace,
        user=user,
        is_active=True,
    ).first()
    if membership is None:
        raise PermissionDenied("User is not an active member of this workspace.")
    return membership


@transaction.atomic
def create_chatbot(
    *,
    workspace,
    name,
    created_by,
    description="",
    instructions="",
):
    _require_active_workspace_user(workspace, created_by)
    if not workspace.is_active:
        raise ValidationError("Cannot create a chatbot in an inactive workspace.")

    chatbot = Chatbot.objects.create(
        workspace=workspace,
        name=name,
        description=description,
        instructions=instructions,
        created_by=created_by,
    )
    ChatbotUser.objects.create(
        chatbot=chatbot,
        user=created_by,
        role=ChatbotRoleTypes.ADMIN,
        created_by=created_by,
    )
    return chatbot


@transaction.atomic
def assign_user_to_chatbot(
    *,
    chatbot,
    user,
    assigned_by,
    role=ChatbotRoleTypes.MEMBER,
):
    """
    Assign a workspace member to a chatbot.

    Any active workspace member may perform the assignment for now; this can be
    narrowed to admins later without changing the membership schema.
    """
    if not chatbot.is_active or not chatbot.workspace.is_active:
        raise ValidationError("Cannot assign users to an inactive chatbot.")
    _require_active_workspace_user(chatbot.workspace, assigned_by)
    _require_active_workspace_user(chatbot.workspace, user)

    role = ChatbotRoleTypes(role)
    membership, created = ChatbotUser.objects.get_or_create(
        chatbot=chatbot,
        user=user,
        defaults={
            "role": role,
            "created_by": assigned_by,
        },
    )
    if not created:
        if not (
            membership.role == ChatbotRoleTypes.ADMIN
            and role == ChatbotRoleTypes.MEMBER
        ):
            membership.role = role
        membership.is_active = True
        membership.updated_by = assigned_by
        membership.full_clean()
        membership.save(
            update_fields=["role", "is_active", "updated_by", "updated_at"]
        )
    return membership
