from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction

from agent.sub_agents.appointment.tools import OPEN_STATUSES, available_slots
from appointment_booking.models import Appointment, AppointmentBookingConfig
from chat.models import ChatSession
from chat.utils.choices import ChatSessionStatus


@transaction.atomic
def book_visitor_appointment(chat_session, *, starts_at, collected_fields):
    """Book one currently available slot for a token-authorized chat session."""
    chat_session = (
        ChatSession.objects.select_for_update()
        .filter(
            pk=chat_session.pk,
            chatbot_id=chat_session.chatbot_id,
            is_test=False,
            status=ChatSessionStatus.OPEN,
        )
        .first()
    )
    if chat_session is None:
        raise ValidationError("This conversation is no longer open.")

    config = (
        AppointmentBookingConfig.objects.select_for_update()
        .select_related("chatbot")
        .filter(chatbot_id=chat_session.chatbot_id, is_enabled=True)
        .first()
    )
    if config is None:
        raise ValidationError("Appointment booking is not available.")

    existing = Appointment.objects.filter(
        chatbot_id=chat_session.chatbot_id,
        metadata__chat_session_id=str(chat_session.id),
        starts_at=starts_at,
        status__in=OPEN_STATUSES,
    ).first()
    if existing is not None:
        return existing, False

    local_day = starts_at.astimezone(ZoneInfo(config.chatbot.timezone)).date()
    selected_slot = next(
        (
            slot
            for slot in available_slots(config, local_day)
            if datetime.fromisoformat(slot["starts_at"]) == starts_at
        ),
        None,
    )
    if selected_slot is None:
        raise ValidationError(
            "The selected appointment slot is no longer available."
        )

    appointment = Appointment(
        chatbot_id=chat_session.chatbot_id,
        collected_fields=collected_fields,
        metadata={
            "chat_session_id": str(chat_session.id),
            "source": "web_widget",
        },
        starts_at=datetime.fromisoformat(selected_slot["starts_at"]),
        ends_at=datetime.fromisoformat(selected_slot["ends_at"]),
    )
    appointment.full_clean()
    appointment.save()
    return appointment, True
