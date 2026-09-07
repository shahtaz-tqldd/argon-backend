from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from chat_session.models import (
    ChatSession,
    ChatSessionTakeover,
    ChatSessionTransfer,
)
from chat_session.services.events import publish_session_event
from chat_session.utils.choices import (
    ChatSessionAttentionReason,
    ChatSessionStatus,
    ChatSessionTakeoverReleaseReason,
    ChatSessionTransferStatus,
)


def _validate_agent(chat_session, agent):
    if not agent.is_active or not agent.user.is_active:
        raise ValidationError("The agent is not active.")
    if agent.chatbot_id != chat_session.chatbot_id:
        raise ValidationError("The agent must belong to this session's chatbot.")


def _locked_session(chat_session):
    return ChatSession.objects.select_for_update().select_related("chatbot").get(
        pk=chat_session.pk
    )


def _active_takeover(chat_session):
    return (
        ChatSessionTakeover.objects.select_for_update()
        .filter(chat_session=chat_session, released_at__isnull=True)
        .first()
    )


def _require_owner(chat_session, agent):
    _validate_agent(chat_session, agent)
    active = _active_takeover(chat_session)
    if active is None:
        raise ValidationError("Session does not have an active takeover.")
    if active.agent_id != agent.id:
        raise ValidationError("Only the current owner can perform this action.")
    return active


def _cancel_pending_transfers(chat_session, now):
    ChatSessionTransfer.objects.filter(
        chat_session=chat_session,
        status=ChatSessionTransferStatus.PENDING,
    ).update(
        status=ChatSessionTransferStatus.CANCELLED,
        completed_at=now,
        updated_at=now,
    )


def take_over_session(chat_session, agent):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        _validate_agent(chat_session, agent)
        if chat_session.status != ChatSessionStatus.OPEN:
            raise ValidationError("Reopen the session before taking it over.")
        if _active_takeover(chat_session) is not None:
            raise ValidationError("Session already has an active takeover.")

        takeover = ChatSessionTakeover(chat_session=chat_session, agent=agent)
        takeover.full_clean()
        takeover.save()
        chat_session.assigned_to = agent
        chat_session.ai_enabled = False
        chat_session.requires_attention = False
        chat_session.attention_reason = ""
        chat_session.attention_requested_at = None
        chat_session.save(
            update_fields=[
                "assigned_to",
                "ai_enabled",
                "requires_attention",
                "attention_reason",
                "attention_requested_at",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.taken_over",
                {"takeover_id": str(takeover.id), "agent_id": str(agent.id)},
            )
        )
    return takeover


def request_transfer(
    chat_session, from_agent, to_agent, *, reason="", expires_at=None
):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        active = _require_owner(chat_session, from_agent)
        _validate_agent(chat_session, to_agent)
        if from_agent.id == to_agent.id:
            raise ValidationError("Cannot transfer a session to the same agent.")
        if expires_at is not None and expires_at <= timezone.now():
            raise ValidationError({"expires_at": "Must be in the future."})
        if ChatSessionTransfer.objects.filter(
            chat_session=chat_session,
            status=ChatSessionTransferStatus.PENDING,
        ).exists():
            raise ValidationError("Session already has a pending transfer.")

        transfer = ChatSessionTransfer(
            chat_session=chat_session,
            from_agent=from_agent,
            to_agent=to_agent,
            reason=reason,
            expires_at=expires_at,
        )
        transfer.full_clean()
        transfer.save()
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.transfer_requested",
                {
                    "transfer_id": str(transfer.id),
                    "takeover_id": str(active.id),
                    "from_agent_id": str(from_agent.id),
                    "to_agent_id": str(to_agent.id),
                },
            )
        )
    return transfer


def _locked_pending_transfer(transfer):
    locked = (
        ChatSessionTransfer.objects.select_for_update()
        .select_related("from_agent__user", "to_agent__user")
        .get(pk=transfer.pk)
    )
    if locked.status != ChatSessionTransferStatus.PENDING:
        raise ValidationError("Transfer is no longer pending.")
    return locked


def accept_transfer(transfer, agent):
    expired = False
    with transaction.atomic():
        transfer = _locked_pending_transfer(transfer)
        chat_session = ChatSession.objects.select_for_update().get(
            pk=transfer.chat_session_id
        )
        _validate_agent(chat_session, agent)
        if transfer.to_agent_id != agent.id:
            raise ValidationError("Only the requested agent can accept this transfer.")

        now = timezone.now()
        if transfer.expires_at and transfer.expires_at <= now:
            transfer.status = ChatSessionTransferStatus.EXPIRED
            transfer.completed_at = now
            transfer.save(update_fields=["status", "completed_at", "updated_at"])
            expired = True
        else:
            active = _active_takeover(chat_session)
            if active is None or active.agent_id != transfer.from_agent_id:
                raise ValidationError("The original agent no longer owns this session.")

            active.released_at = now
            active.release_reason = ChatSessionTakeoverReleaseReason.TRANSFERRED
            active.released_to = agent
            active.full_clean()
            active.save(
                update_fields=[
                    "released_at",
                    "release_reason",
                    "released_to",
                    "updated_at",
                ]
            )
            takeover = ChatSessionTakeover(chat_session=chat_session, agent=agent)
            takeover.full_clean()
            takeover.save()
            transfer.status = ChatSessionTransferStatus.ACCEPTED
            transfer.completed_at = now
            transfer.save(update_fields=["status", "completed_at", "updated_at"])
            chat_session.assigned_to = agent
            chat_session.ai_enabled = False
            chat_session.save(
                update_fields=["assigned_to", "ai_enabled", "updated_at"]
            )
            transaction.on_commit(
                lambda: publish_session_event(
                    chat_session.id,
                    "session.transferred",
                    {
                        "transfer_id": str(transfer.id),
                        "takeover_id": str(takeover.id),
                        "from_agent_id": str(transfer.from_agent_id),
                        "to_agent_id": str(agent.id),
                    },
                )
            )
    if expired:
        raise ValidationError("Transfer has expired.")
    return transfer


def decline_transfer(transfer, agent):
    expired = False
    with transaction.atomic():
        transfer = _locked_pending_transfer(transfer)
        chat_session = ChatSession.objects.select_for_update().get(
            pk=transfer.chat_session_id
        )
        _validate_agent(chat_session, agent)
        if transfer.to_agent_id != agent.id:
            raise ValidationError("Only the requested agent can decline this transfer.")
        now = timezone.now()
        transfer.status = (
            ChatSessionTransferStatus.EXPIRED
            if transfer.expires_at and transfer.expires_at <= now
            else ChatSessionTransferStatus.DECLINED
        )
        expired = transfer.status == ChatSessionTransferStatus.EXPIRED
        transfer.completed_at = now
        transfer.save(update_fields=["status", "completed_at", "updated_at"])
        if not expired:
            transaction.on_commit(
                lambda: publish_session_event(
                    chat_session.id,
                    "session.transfer_declined",
                    {"transfer_id": str(transfer.id)},
                )
            )
    if expired:
        raise ValidationError("Transfer has expired.")
    return transfer


def cancel_transfer(transfer, agent):
    with transaction.atomic():
        transfer = _locked_pending_transfer(transfer)
        chat_session = ChatSession.objects.select_for_update().get(
            pk=transfer.chat_session_id
        )
        _validate_agent(chat_session, agent)
        if transfer.from_agent_id != agent.id:
            raise ValidationError("Only the requesting agent can cancel this transfer.")
        now = timezone.now()
        transfer.status = ChatSessionTransferStatus.CANCELLED
        transfer.completed_at = now
        transfer.save(update_fields=["status", "completed_at", "updated_at"])
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.transfer_cancelled",
                {"transfer_id": str(transfer.id)},
            )
        )
    return transfer


def expire_pending_transfers(queryset):
    now = timezone.now()
    return queryset.filter(
        status=ChatSessionTransferStatus.PENDING,
        expires_at__isnull=False,
        expires_at__lte=now,
    ).update(
        status=ChatSessionTransferStatus.EXPIRED,
        completed_at=now,
        updated_at=now,
    )


def release_session(chat_session, agent):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        active = _require_owner(chat_session, agent)
        now = timezone.now()
        active.released_at = now
        active.release_reason = ChatSessionTakeoverReleaseReason.RELEASED
        active.full_clean()
        active.save(update_fields=["released_at", "release_reason", "updated_at"])
        _cancel_pending_transfers(chat_session, now)
        chat_session.assigned_to = None
        chat_session.ai_enabled = chat_session.chatbot.ai_enabled
        chat_session.save(update_fields=["assigned_to", "ai_enabled", "updated_at"])
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.released",
                {"takeover_id": str(active.id)},
            )
        )
    return active


def resolve_session(chat_session, agent, resolution_type, note=""):
    if resolution_type not in {
        ChatSessionTakeoverReleaseReason.RESOLVED,
        ChatSessionTakeoverReleaseReason.CLOSED,
    }:
        raise ValidationError("Invalid session resolution type.")

    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        active = _require_owner(chat_session, agent)
        now = timezone.now()
        active.released_at = now
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
        _cancel_pending_transfers(chat_session, now)
        chat_session.status = resolution_type
        chat_session.assigned_to = None
        chat_session.ai_enabled = False
        chat_session.requires_attention = False
        chat_session.attention_reason = ""
        chat_session.attention_requested_at = None
        chat_session.resolved_at = (
            now if resolution_type == ChatSessionStatus.RESOLVED else None
        )
        chat_session.closed_at = (
            now if resolution_type == ChatSessionStatus.CLOSED else None
        )
        chat_session.save(
            update_fields=[
                "status",
                "assigned_to",
                "ai_enabled",
                "requires_attention",
                "attention_reason",
                "attention_requested_at",
                "resolved_at",
                "closed_at",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                f"session.{resolution_type}",
                {"takeover_id": str(active.id), "status": resolution_type},
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

        now = timezone.now()
        last.reopened_at = now
        last.full_clean()
        last.save(update_fields=["reopened_at", "updated_at"])
        chat_session.status = ChatSessionStatus.OPEN
        chat_session.resolved_at = None
        chat_session.closed_at = None
        chat_session.requires_attention = True
        chat_session.attention_reason = ChatSessionAttentionReason.OTHER
        chat_session.attention_requested_at = now
        chat_session.ai_enabled = chat_session.chatbot.ai_enabled
        chat_session.save(
            update_fields=[
                "status",
                "resolved_at",
                "closed_at",
                "requires_attention",
                "attention_reason",
                "attention_requested_at",
                "ai_enabled",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                "session.reopened",
                {"reopened_by_id": str(agent.id)},
            )
        )
    return last
