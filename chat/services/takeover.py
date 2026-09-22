from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from chat.models import (
    ChatSession,
    ChatSessionTakeover,
    ChatSessionTransfer,
)
from chat.services.events import publish_session_event
from chat.utils.choices import (
    ChatSessionStatus,
    ChatSessionTakeoverReleaseReason,
    ChatSessionTransferStatus,
)
from notification.models import NotificationType
from notification.services import create_user_notification


def _agent_session_state(agent):
    if agent is None:
        return None
    return {
        "id": str(agent.id),
        "name": agent.user.name,
    }


def _session_management_state(chat_session, *, pending_transfer=None):
    """Return the patch every dashboard needs after an ownership change."""
    return {
        "status": chat_session.status,
        "ai_enabled": chat_session.ai_enabled,
        "assigned_to": _agent_session_state(chat_session.assigned_to),
        "has_pending_transfer": pending_transfer is not None,
        "transfer_requested_to": (
            _agent_session_state(pending_transfer.to_agent)
            if pending_transfer is not None
            else None
        ),
    }


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


def _notify_transfer_agent(
    transfer,
    recipient_agent,
    actor_agent,
    *,
    title,
    message,
):
    create_user_notification(
        recipient=recipient_agent.user,
        notification_type=NotificationType.NOTIFY,
        title=title,
        message=message,
        metadata={
            "kind": "session_transfer",
            "status": transfer.status,
            "transfer_id": str(transfer.id),
            "chat_session_id": str(transfer.chat_session_id),
            "chatbot_id": str(recipient_agent.chatbot_id),
            "from_agent_id": str(transfer.from_agent_id),
            "to_agent_id": str(transfer.to_agent_id),
        },
        created_by=actor_agent.user,
    )


def take_over_session(
    chat_session,
    agent,
    *,
    is_forced=False,
    takeover_reason="",
):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        _validate_agent(chat_session, agent)
        if chat_session.status != ChatSessionStatus.OPEN:
            raise ValidationError("Reopen the session before taking it over.")
        pending_transfer = (
            ChatSessionTransfer.objects.select_for_update()
            .filter(
                chat_session=chat_session,
                status=ChatSessionTransferStatus.PENDING,
            )
            .first()
        )
        if pending_transfer is not None and not is_forced:
            raise ValidationError(
                "Session cannot be taken over while a transfer is pending."
            )
        active = _active_takeover(chat_session)
        if active is not None and (
            not is_forced
            or pending_transfer is None
            or active.agent_id == agent.id
        ):
            raise ValidationError("Session already has an active takeover.")

        now = timezone.now()
        if pending_transfer is not None:
            pending_transfer.status = ChatSessionTransferStatus.CANCELLED
            pending_transfer.completed_at = now
            pending_transfer.save(
                update_fields=["status", "completed_at", "updated_at"]
            )
            transaction.on_commit(
                lambda: publish_session_event(
                    chat_session.id,
                    chat_session.chatbot_id,
                    "session.transfer_cancelled",
                    {
                        "transfer_id": str(pending_transfer.id),
                        "transfer_status": pending_transfer.status,
                        "forced_takeover": True,
                        **_session_management_state(chat_session),
                    },
                )
            )

        if active is not None:
            active.released_at = now
            active.release_reason = (
                ChatSessionTakeoverReleaseReason.FORCED_TAKEOVER
            )
            active.full_clean()
            active.save(
                update_fields=["released_at", "release_reason", "updated_at"]
            )

        takeover = ChatSessionTakeover(
            chat_session=chat_session,
            agent=agent,
            is_forced=is_forced,
            takeover_reason=takeover_reason,
        )
        takeover.full_clean()
        takeover.save()
        chat_session.assigned_to = agent
        chat_session.ai_enabled = False
        chat_session.save(
            update_fields=[
                "assigned_to",
                "ai_enabled",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                chat_session.chatbot_id,
                "session.taken_over",
                {
                    "takeover_id": str(takeover.id),
                    "agent_id": str(agent.id),
                    "is_forced": takeover.is_forced,
                    "takeover_reason": takeover.takeover_reason,
                    **_session_management_state(chat_session),
                },
            )
        )
    return takeover


def request_transfer(
    chat_session, from_agent, to_agent, *, reason="", expires_at=None
):
    with transaction.atomic():
        chat_session = _locked_session(chat_session)
        _validate_agent(chat_session, from_agent)
        active = _active_takeover(chat_session)
        if active is not None and active.agent_id != from_agent.id:
            raise ValidationError("Only the current owner can perform this action.")
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
        _notify_transfer_agent(
            transfer,
            to_agent,
            from_agent,
            title="Session transfer requested",
            message=f"{from_agent.user} requested that you take over a session.",
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                chat_session.chatbot_id,
                "session.transfer_requested",
                {
                    "transfer_id": str(transfer.id),
                    "takeover_id": str(active.id) if active is not None else None,
                    "from_agent_id": str(from_agent.id),
                    "to_agent_id": str(to_agent.id),
                    "transfer_status": transfer.status,
                    **_session_management_state(
                        chat_session,
                        pending_transfer=transfer,
                    ),
                },
            )
        )
    return transfer


def _locked_pending_transfer(transfer):
    # Keep nullable profile joins out of this query. PostgreSQL cannot apply
    # FOR UPDATE to the nullable side of an outer join; related display data
    # can be loaded separately when the response is serialized.
    locked = ChatSessionTransfer.objects.select_for_update().get(pk=transfer.pk)
    if locked.status != ChatSessionTransferStatus.PENDING:
        raise ValidationError("Transfer is no longer pending.")
    return locked


def accept_transfer(transfer, agent):
    expired = False
    with transaction.atomic():
        chat_session = ChatSession.objects.select_for_update().get(
            pk=transfer.chat_session_id
        )
        transfer = _locked_pending_transfer(transfer)
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
            if active is not None and active.agent_id != transfer.from_agent_id:
                raise ValidationError("The original agent no longer owns this session.")

            if active is not None:
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
            _notify_transfer_agent(
                transfer,
                transfer.from_agent,
                agent,
                title="Session transfer accepted",
                message=f"{agent.user} accepted your session transfer request.",
            )
            transaction.on_commit(
                lambda: publish_session_event(
                    chat_session.id,
                    chat_session.chatbot_id,
                    "session.transferred",
                    {
                        "transfer_id": str(transfer.id),
                        "takeover_id": str(takeover.id),
                        "from_agent_id": str(transfer.from_agent_id),
                        "to_agent_id": str(agent.id),
                        "transfer_status": transfer.status,
                        **_session_management_state(chat_session),
                    },
                )
            )
    if expired:
        raise ValidationError("Transfer has expired.")
    return transfer


def decline_transfer(transfer, agent):
    expired = False
    with transaction.atomic():
        chat_session = ChatSession.objects.select_for_update().get(
            pk=transfer.chat_session_id
        )
        transfer = _locked_pending_transfer(transfer)
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
            _notify_transfer_agent(
                transfer,
                transfer.from_agent,
                agent,
                title="Session transfer declined",
                message=f"{agent.user} declined your session transfer request.",
            )
            transaction.on_commit(
                lambda: publish_session_event(
                    chat_session.id,
                    chat_session.chatbot_id,
                    "session.transfer_declined",
                    {
                        "transfer_id": str(transfer.id),
                        "transfer_status": transfer.status,
                        **_session_management_state(chat_session),
                    },
                )
            )
    if expired:
        raise ValidationError("Transfer has expired.")
    return transfer


def cancel_transfer(transfer, agent):
    with transaction.atomic():
        chat_session = ChatSession.objects.select_for_update().get(
            pk=transfer.chat_session_id
        )
        transfer = _locked_pending_transfer(transfer)
        _validate_agent(chat_session, agent)
        if transfer.from_agent_id != agent.id:
            raise ValidationError("Only the requesting agent can cancel this transfer.")
        now = timezone.now()
        transfer.status = ChatSessionTransferStatus.CANCELLED
        transfer.completed_at = now
        transfer.save(update_fields=["status", "completed_at", "updated_at"])
        _notify_transfer_agent(
            transfer,
            transfer.to_agent,
            agent,
            title="Session transfer cancelled",
            message=f"{agent.user} cancelled the session transfer request.",
        )
        transaction.on_commit(
            lambda: publish_session_event(
                chat_session.id,
                chat_session.chatbot_id,
                "session.transfer_cancelled",
                {
                    "transfer_id": str(transfer.id),
                    "transfer_status": transfer.status,
                    **_session_management_state(chat_session),
                },
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
                chat_session.chatbot_id,
                "session.released",
                {
                    "takeover_id": str(active.id),
                    **_session_management_state(chat_session),
                },
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
                chat_session.chatbot_id,
                f"session.{resolution_type}",
                {
                    "takeover_id": str(active.id),
                    **_session_management_state(chat_session),
                },
            )
        )
    return active
