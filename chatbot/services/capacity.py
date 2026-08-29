from django.core.exceptions import ValidationError
from django.db import transaction

from chatbot.models import ChatbotCapacity
from chatbot.services.resolution import resolve_chatbot_reference
from chatbot.services.subscription import get_chatbot_subscription_entitlements
from subscription.choices import PlanFeature


BYTES_PER_MEGABYTE = 1024 * 1024
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
    return ChatbotCapacity.objects.get(chatbot_id=chatbot.id)


def _normalized_features(features):
    try:
        normalized = [PlanFeature(feature).value for feature in features]
    except ValueError as exc:
        raise ValidationError(
            {"active_features": f"Unknown plan feature: {exc}."}
        ) from exc
    if len(normalized) != len(set(normalized)):
        raise ValidationError(
            {"active_features": "Active features must be unique."}
        )
    return normalized


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
    ai_message_limit=UNSET,
    file_size_limit_bytes=UNSET,
    knowledge_chunk_limit=UNSET,
    active_features=UNSET,
    current_ai_message_count=UNSET,
    current_file_size_bytes=UNSET,
    current_knowledge_chunk_count=UNSET,
    ai_message_delta=0,
    file_size_delta_bytes=0,
    knowledge_chunk_delta=0,
):
    """Create or atomically update a chatbot's cached limits and usage."""

    chatbot = resolve_chatbot_reference(
        chatbot,
        chatbot_slug=chatbot_slug,
        chatbot_id=chatbot_id,
    )
    with transaction.atomic():
        capacity, created = ChatbotCapacity.objects.get_or_create(chatbot=chatbot)
        if not created:
            capacity = ChatbotCapacity.objects.select_for_update().get(
                pk=capacity.pk
            )

        if ai_message_limit is not UNSET:
            capacity.ai_message_limit = ai_message_limit
        if file_size_limit_bytes is not UNSET:
            capacity.file_size_limit_bytes = file_size_limit_bytes
        if knowledge_chunk_limit is not UNSET:
            capacity.knowledge_chunk_limit = knowledge_chunk_limit
        if active_features is not UNSET:
            capacity.active_features = _normalized_features(active_features)

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


def sync_chatbot_capacity_from_subscription(
    chatbot=None,
    *,
    chatbot_slug=None,
    chatbot_id=None,
):
    """Copy active subscription limits/features while preserving usage."""

    chatbot = resolve_chatbot_reference(
        chatbot,
        chatbot_slug=chatbot_slug,
        chatbot_id=chatbot_id,
    )
    entitlements = get_chatbot_subscription_entitlements(chatbot)
    file_size_limit_mb = entitlements.file_size_limit_mb

    return update_chatbot_capacity(
        chatbot,
        ai_message_limit=entitlements.ai_message_limit,
        file_size_limit_bytes=(
            file_size_limit_mb * BYTES_PER_MEGABYTE
            if file_size_limit_mb is not None
            else None
        ),
        knowledge_chunk_limit=entitlements.knowledge_chunk_limit,
        active_features=entitlements.features,
    )
