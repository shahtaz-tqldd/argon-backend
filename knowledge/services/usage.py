from django.db.models import Sum

from knowledge.models import KnowledgeBase
from subscription.choices import PlanFeature, SubscriptionStatus
from subscription.models import ChatbotSubscription
from vector_store.models import VectorDocument


BYTES_PER_MEGABYTE = 1024 * 1024


class KnowledgeEntitlementError(Exception):
    """Raised when a chatbot cannot use its subscription's knowledge base."""


class KnowledgeLimitExceeded(Exception):
    """Raised when knowledge storage would exceed the subscription snapshot."""


def get_knowledge_subscription(chatbot, *, for_update=False):
    subscriptions = ChatbotSubscription.objects.filter(
        chatbot_id=chatbot.id,
        status=SubscriptionStatus.ACTIVE,
    ).order_by("-created_at")
    if for_update:
        subscriptions = subscriptions.select_for_update()

    subscription = subscriptions.first()
    if subscription is None:
        raise KnowledgeEntitlementError(
            "An active subscription is required to use the knowledge base."
        )
    if not subscription.has_feature(PlanFeature.KNOWLEDGE_BASE):
        raise KnowledgeEntitlementError(
            "The active subscription does not include the knowledge base feature."
        )
    return subscription


def get_knowledge_usage(chatbot):
    subscription = get_knowledge_subscription(chatbot)
    file_size_limit_mb = subscription.get_file_size_limit_mb()
    total_file_size_bytes = KnowledgeBase.objects.filter(
        chatbot_id=chatbot.id,
    ).aggregate(
        total=Sum("file_size", default=0),
    )["total"]
    total_chunks = VectorDocument.objects.filter(
        knowledge_base__chatbot_id=chatbot.id,
    ).count()

    return {
        "total_chunks": total_chunks,
        "chunk_limit": subscription.get_knowledge_chunk_limit(),
        "total_file_size_bytes": total_file_size_bytes,
        "file_size_limit_bytes": (
            file_size_limit_mb * BYTES_PER_MEGABYTE
            if file_size_limit_mb is not None
            else None
        ),
        "file_size_limit_mb": file_size_limit_mb,
    }


def validate_knowledge_file_capacity(chatbot, incoming_file_size):
    subscription = get_knowledge_subscription(chatbot)
    limit_mb = subscription.get_file_size_limit_mb()
    if limit_mb is None:
        return

    current_size = KnowledgeBase.objects.filter(
        chatbot_id=chatbot.id,
    ).aggregate(
        total=Sum("file_size", default=0),
    )["total"]
    limit_bytes = limit_mb * BYTES_PER_MEGABYTE
    if current_size + incoming_file_size > limit_bytes:
        raise KnowledgeLimitExceeded(
            f"This subscription allows {limit_mb} MB of knowledge files."
        )


def validate_knowledge_chunk_capacity(
    knowledge_base,
    incoming_chunk_count,
    *,
    lock_subscription=False,
):
    subscription = get_knowledge_subscription(
        knowledge_base.chatbot,
        for_update=lock_subscription,
    )
    limit = subscription.get_knowledge_chunk_limit()
    if limit is None:
        return

    existing_chunks = (
        VectorDocument.objects.filter(
            knowledge_base__chatbot_id=knowledge_base.chatbot_id,
        )
        .exclude(knowledge_base_id=knowledge_base.id)
        .count()
    )
    if existing_chunks + incoming_chunk_count > limit:
        raise KnowledgeLimitExceeded(
            f"This subscription allows {limit} knowledge chunks; "
            f"{existing_chunks} are already used by other sources and this "
            f"source requires {incoming_chunk_count}."
        )
