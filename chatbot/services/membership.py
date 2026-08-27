from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from chatbot.models import (
    DEFAULT_CHATBOT_ESCALATION_RULE,
    DEFAULT_CHATBOT_FALLBACK_MESSAGE,
    DEFAULT_CHATBOT_NEVER_ANSWER,
    Chatbot,
    ChatbotUser,
    ChatbotWidgetSettings,
    build_default_chatbot_welcome_message,
)
from chatbot.utils.choices import ChatbotRoleTypes, ChatbotStatusTypes
from chatbot.utils.validation import validate_unique_chatbot_name
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
    chatbot_name,
    created_by,
    business_name="",
    description="",
    welcome_message=None,
    fallback_message=DEFAULT_CHATBOT_FALLBACK_MESSAGE,
    instructions="",
    escalation_rule=DEFAULT_CHATBOT_ESCALATION_RULE,
    never_answer=DEFAULT_CHATBOT_NEVER_ANSWER,
    language="en",
    timezone="UTC",
    ai_enabled=True,
    knowledge_base_enabled=True,
    human_handoff_enabled=True,
    other_settings=None,
    logo="",
    status=ChatbotStatusTypes.DRAFT,
):
    _require_active_workspace_user(workspace, created_by)
    if not workspace.is_active:
        raise ValidationError("Cannot create a chatbot in an inactive workspace.")

    chatbot_name = chatbot_name.strip()
    if not chatbot_name:
        raise ValidationError("Chatbot name is required.")
    validate_unique_chatbot_name(
        workspace=workspace,
        chatbot_name=chatbot_name,
    )
    if welcome_message is None:
        welcome_message = build_default_chatbot_welcome_message(
            chatbot_name,
            business_name,
        )

    chatbot = Chatbot.objects.create(
        workspace=workspace,
        chatbot_name=chatbot_name,
        business_name=business_name,
        description=description,
        welcome_message=welcome_message,
        fallback_message=fallback_message,
        instructions=instructions,
        escalation_rule=escalation_rule,
        never_answer=never_answer,
        language=language,
        timezone=timezone,
        ai_enabled=ai_enabled,
        knowledge_base_enabled=knowledge_base_enabled,
        human_handoff_enabled=human_handoff_enabled,
        other_settings={} if other_settings is None else other_settings,
        logo=logo,
        status=status,
        created_by=created_by,
        is_deleted=False,
    )
    ChatbotWidgetSettings.objects.create(
        chatbot=chatbot,
        created_by=created_by,
    )
    ChatbotUser.objects.create(
        chatbot=chatbot,
        user=created_by,
        role=ChatbotRoleTypes.ADMIN,
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
        defaults={"role": role},
    )
    if not created:
        if not (
            membership.role == ChatbotRoleTypes.ADMIN
            and role == ChatbotRoleTypes.MEMBER
        ):
            membership.role = role
        membership.is_active = True
        membership.full_clean()
        membership.save(update_fields=["role", "is_active", "updated_at"])
    return membership
