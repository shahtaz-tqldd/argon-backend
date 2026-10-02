from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from appointment.models import AppointmentBookingConfig
from chatbot.models import ChatbotConfig
from chatbot.services.resolution import resolve_chatbot_reference
from lead_capture.models import LeadCaptureConfig
from subscription.choices import PlanFeature, SubscriptionStatus
from subscription.models import ChatbotSubscription


CAPACITY_APPLIED_METADATA_KEY = "chatbot_capacity_applied_at"
UNSET = object()


def get_chatbot_capacity(
    chatbot=None,
    *,
    chatbot_slug=None,
    chatbot_id=None,
):
    chatbot = resolve_chatbot_reference(
        chatbot,
        chatbot_slug=chatbot_slug,
        chatbot_id=chatbot_id,
    )
    return ChatbotConfig.objects.get(chatbot_id=chatbot.id)


def chatbot_has_feature(chatbot, feature):
    """
    Return whether the chatbot's current subscription enables ``feature``.
    """
    try:
        capacity = chatbot.capacity

    except (AttributeError, ChatbotConfig.DoesNotExist):
        return False

    return capacity.has_feature(feature)


def _updated_count(*, current, absolute, delta, field_name):
    if isinstance(delta, bool) or not isinstance(delta, int):
        raise ValidationError({field_name: "Usage delta must be an integer."})
    if absolute is not UNSET and delta:
        raise ValidationError(
            {field_name: "Provide an absolute value or a delta, not both."}
        )
    value = absolute if absolute is not UNSET else current + delta
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(
            {field_name: "Current usage must be a non-negative integer."}
        )
    return value


def update_chatbot_capacity(
    chatbot=None,
    *,
    chatbot_slug=None,
    chatbot_id=None,
    current_ai_message_count=UNSET,
    current_file_size_bytes=UNSET,
    current_knowledge_chunk_count=UNSET,
    ai_message_delta=0,
    file_size_delta_bytes=0,
    knowledge_chunk_delta=0,
):
    """Create or atomically update a chatbot's tracked usage counters."""

    chatbot = resolve_chatbot_reference(
        chatbot,
        chatbot_slug=chatbot_slug,
        chatbot_id=chatbot_id,
    )
    with transaction.atomic():
        capacity, created = ChatbotConfig.objects.get_or_create(chatbot=chatbot)
        if not created:
            capacity = ChatbotConfig.objects.select_for_update().get(
                pk=capacity.pk
            )

        capacity.current_ai_message_count = _updated_count(
            current=capacity.current_ai_message_count,
            absolute=current_ai_message_count,
            delta=ai_message_delta,
            field_name="current_ai_message_count",
        )
        capacity.current_file_size_bytes = _updated_count(
            current=capacity.current_file_size_bytes,
            absolute=current_file_size_bytes,
            delta=file_size_delta_bytes,
            field_name="current_file_size_bytes",
        )
        capacity.current_knowledge_chunk_count = _updated_count(
            current=capacity.current_knowledge_chunk_count,
            absolute=current_knowledge_chunk_count,
            delta=knowledge_chunk_delta,
            field_name="current_knowledge_chunk_count",
        )

        capacity.full_clean()
        capacity.save()
        return capacity


@transaction.atomic
def apply_active_subscription_to_chatbot_capacity(subscription):
    """Apply one newly activated subscription to chatbot capacity.

    Each subscription contract is applied once. Limits and features are
    derived from the contract snapshot, so only usage is adjusted here: a
    free plan resets the message usage, while feature-dependent configs are
    enabled or disabled to match the contract.
    """

    subscription = (
        ChatbotSubscription.objects.select_for_update()
        .select_related("chatbot")
        .get(pk=subscription.pk)
    )
    if subscription.status != SubscriptionStatus.ACTIVE:
        raise ValidationError(
            "Only an active subscription can be applied to chatbot capacity."
        )

    metadata = subscription.provider_metadata or {}
    if metadata.get(CAPACITY_APPLIED_METADATA_KEY):
        return ChatbotConfig.objects.get(chatbot_id=subscription.chatbot_id)

    capacity, created = ChatbotConfig.objects.get_or_create(
        chatbot_id=subscription.chatbot_id
    )
    if not created:
        capacity = ChatbotConfig.objects.select_for_update().get(
            pk=capacity.pk
        )

    if subscription.is_free_plan():
        capacity.current_ai_message_count = 0
        capacity.full_clean()
        capacity.save()

    if (
        not subscription.is_free_plan()
        and subscription.has_feature(PlanFeature.LEAD_CAPTURE)
    ):
        LeadCaptureConfig.objects.get_or_create(
            chatbot_id=subscription.chatbot_id,
            defaults={
                "created_by": subscription.selected_by,
                "updated_by": subscription.selected_by,
            },
        )
    else:
        LeadCaptureConfig.objects.filter(
            chatbot_id=subscription.chatbot_id,
            is_enabled=True,
        ).update(
            is_enabled=False,
            updated_by=subscription.selected_by,
            updated_at=timezone.now(),
        )

    if (
        not subscription.is_free_plan()
        and subscription.has_feature(PlanFeature.APPOINTMENT_BOOKING)
    ):
        AppointmentBookingConfig.objects.get_or_create(
            chatbot_id=subscription.chatbot_id,
            defaults={
                "created_by": subscription.selected_by,
                "updated_by": subscription.selected_by,
            },
        )
    else:
        AppointmentBookingConfig.objects.filter(
            chatbot_id=subscription.chatbot_id,
            is_enabled=True,
        ).update(
            is_enabled=False,
            updated_by=subscription.selected_by,
            updated_at=timezone.now(),
        )

    subscription.provider_metadata = {
        **metadata,
        CAPACITY_APPLIED_METADATA_KEY: timezone.now().isoformat(),
    }
    subscription.save(update_fields=["provider_metadata", "updated_at"])
    return capacity
