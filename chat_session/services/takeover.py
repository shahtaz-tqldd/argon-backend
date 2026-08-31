from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from chat_session.models import ChatSession, ChatSessionTakeover
from chat_session.services.events import publish_session_event
from chat_session.utils.choices import (
    ChatSessionStatus,
    ChatSessionTakeoverReleaseReason,
)


def _validate_agent(chat_session, agent):
    if not agent.is_active or not agent.user.is_active:
        raise ValidationError("The agent is not active.")
    if agent.chatbot_id != chat_session.chatbot_id:
        raise ValidationError("The agent must belong to this session's chatbot.")


def _locked_session(chat_session):
    return ChatSession.objects.select_for_update().get(pk=chat_session.pk)


def take_over_session(chat_session, agent):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        _validate_agent(chat_session, agent)
        if chat_session.status in {
            ChatSessionStatus.RESOLVED,
            ChatSessionStatus.CLOSED,
        }:
            raise ValidationError("Reopen the session before taking it over.")
        if ChatSessionTakeover.objects.filter(
            chat_session=chat_session,
            released_at__isnull=True,
        ).exists():
            raise ValidationError("Session already has an active takeover.")

        takeover = ChatSessionTakeover(chat_session=chat_session, agent=agent)
        takeover.full_clean()
        takeover.save()
        chat_session.assigned_to = agent
        chat_session.ai_enabled = False
        chat_session.save(
            update_fields=["assigned_to", "ai_enabled", "updated_at"]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.taken_over",
                {"takeover_id": str(takeover.id), "agent_id": str(agent.id)},
            )
        )
    return takeover


def reassign_session(chat_session, new_agent):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        _validate_agent(chat_session, new_agent)
        active = ChatSessionTakeover.objects.select_for_update().filter(
            chat_session=chat_session,
            released_at__isnull=True,
        ).first()
        if active is None:
            raise ValidationError("Session does not have an active takeover.")
        if active.agent_id == new_agent.id:
            raise ValidationError("Session is already assigned to this agent.")

        active.released_at = timezone.now()
        active.release_reason = ChatSessionTakeoverReleaseReason.REASSIGNED
        active.full_clean()
        active.save(update_fields=["released_at", "release_reason", "updated_at"])

        takeover = ChatSessionTakeover(
            chat_session=chat_session,
            agent=new_agent,
        )
        takeover.full_clean()
        takeover.save()
        chat_session.assigned_to = new_agent
        chat_session.ai_enabled = False
        chat_session.save(
            update_fields=["assigned_to", "ai_enabled", "updated_at"]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.reassigned",
                {
                    "takeover_id": str(takeover.id),
                    "agent_id": str(new_agent.id),
                },
            )
        )
    return takeover


def release_session(chat_session):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        active = ChatSessionTakeover.objects.select_for_update().filter(
            chat_session=chat_session,
            released_at__isnull=True,
        ).first()
        if active is None:
            raise ValidationError("Session does not have an active takeover.")

        active.released_at = timezone.now()
        active.release_reason = ChatSessionTakeoverReleaseReason.MANUAL_RELEASE
        active.full_clean()
        active.save(update_fields=["released_at", "release_reason", "updated_at"])
        chat_session.assigned_to = None
        chat_session.ai_enabled = True
        chat_session.save(
            update_fields=["assigned_to", "ai_enabled", "updated_at"]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.released",
                {"takeover_id": str(active.id)},
            )
        )
    return active


def resolve_session(chat_session, resolution_type, note=""):
    if resolution_type not in {
        ChatSessionTakeoverReleaseReason.RESOLVED,
        ChatSessionTakeoverReleaseReason.CLOSED,
    }:
        raise ValidationError("Invalid session resolution type.")

    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        active = ChatSessionTakeover.objects.select_for_update().filter(
            chat_session=chat_session,
            released_at__isnull=True,
        ).first()
        if active is None:
            raise ValidationError(
                "Session must have an active takeover before it can be resolved."
            )

        active.released_at = timezone.now()
        active.release_reason = resolution_type
        active.resolution_note = note
        active.full_clean()
        active.save(
            update_fields=[
                "released_at",
                "release_reason",
                "resolution_note",
                "updated_at",
            ]
        )
        chat_session.status = resolution_type
        chat_session.assigned_to = None
        chat_session.ai_enabled = False
        chat_session.ended_at = active.released_at
        chat_session.save(
            update_fields=[
                "status",
                "assigned_to",
                "ai_enabled",
                "ended_at",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                f"session.{resolution_type}",
                {
                    "takeover_id": str(active.id),
                    "status": resolution_type,
                },
            )
        )
    return active


def reopen_session(chat_session, agent):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        _validate_agent(chat_session, agent)
        if chat_session.status not in {
            ChatSessionStatus.RESOLVED,
            ChatSessionStatus.CLOSED,
        }:
            raise ValidationError("Only a resolved or closed session can be reopened.")

        last = (
            ChatSessionTakeover.objects.select_for_update()
            .filter(
                chat_session=chat_session,
                release_reason__in=[
                    ChatSessionTakeoverReleaseReason.RESOLVED,
                    ChatSessionTakeoverReleaseReason.CLOSED,
                ],
            )
            .order_by("-released_at")
            .first()
        )
        if last is None:
            raise ValidationError("Session has never been resolved.")

        last.reopened_at = timezone.now()
        last.reopened_by = agent
        last.full_clean()
        last.save(update_fields=["reopened_at", "reopened_by", "updated_at"])
        chat_session.status = ChatSessionStatus.NEED_ATTENTION
        chat_session.ended_at = None
        chat_session.ai_enabled = True
        chat_session.save(
            update_fields=["status", "ended_at", "ai_enabled", "updated_at"]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.reopened",
                {"reopened_by_id": str(agent.id)},
            )
        )
    return last
