from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from subscription.utils.choices import (
    BillingInterval,
    EnterprisePlanRequestStatus,
    PaymentProvider,
    PlanType,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import (
    ChatbotSubscription,
    EnterprisePlanRequest,
    PlanPrice,
    SubscriptionPlan,
)
from subscription.services.subscriptions import OPEN_SUBSCRIPTION_STATUSES


class EnterprisePlanRequestError(Exception):
    """Base error for the enterprise plan request workflow."""


class DuplicatePendingRequestError(EnterprisePlanRequestError):
    """Raised when a chatbot already has a pending enterprise request."""


class RequestNotPendingError(EnterprisePlanRequestError):
    """Raised when a request that is not pending is reviewed again."""


def _apply_subscription_capacity(subscription):
    # Imported lazily to avoid the chatbot/subscription service import cycle.
    from chatbot.services.chatbot_config import (
        apply_active_subscription_to_chatbot_capacity,
    )

    return apply_active_subscription_to_chatbot_capacity(subscription)


def create_enterprise_plan_request(
    *,
    chatbot,
    user,
    requested_features=None,
    requested_ai_message_limit=None,
    requested_file_size_limit_mb=None,
    requested_knowledge_chunk_limit=None,
    requested_daily_traffic=None,
    notes="",
):
    """Store one pending enterprise request per chatbot.

    The partial unique index on (chatbot, status=pending) is the race-safe
    guard; the IntegrityError is translated into a domain error.
    """

    request = EnterprisePlanRequest(
        chatbot=chatbot,
        requested_features=list(requested_features or []),
        requested_ai_message_limit=requested_ai_message_limit,
        requested_file_size_limit_mb=requested_file_size_limit_mb,
        requested_knowledge_chunk_limit=requested_knowledge_chunk_limit,
        requested_daily_traffic=requested_daily_traffic,
        notes=notes or "",
        created_by=user,
        updated_by=user,
    )
    try:
        with transaction.atomic():
            request.full_clean()
            request.save()
    except IntegrityError as exc:
        raise DuplicatePendingRequestError(
            "This chatbot already has a pending enterprise plan request."
        ) from exc
    except ValidationError as exc:
        if _is_duplicate_pending_violation(exc):
            raise DuplicatePendingRequestError(
                "This chatbot already has a pending enterprise plan request."
            ) from exc
        raise
    return request


def _is_duplicate_pending_violation(exc):
    messages = exc.message_dict.get("__all__", [])
    return any(
        "sub_epr_one_pending_per_chatbot" in str(message)
        for message in messages
    )


@transaction.atomic
def approve_enterprise_plan_request(
    *,
    request,
    user,
    features=None,
    ai_message_limit=None,
    file_size_limit_mb=None,
    knowledge_chunk_limit=None,
    expires_at,
    review_notes="",
):
    """Approve a request and materialize its subscription contract.

    Runs in one transaction: the request row is locked so a concurrent
    review cannot double-grant, any open subscription is closed, a private
    CUSTOM plan with a manual price backs the new contract, and the
    subscription snapshot becomes the immutable entitlement source.
    """

    locked_request = (
        EnterprisePlanRequest.objects.select_for_update()
        .select_related("chatbot")
        .get(pk=request.pk)
    )
    if locked_request.status != EnterprisePlanRequestStatus.PENDING:
        raise RequestNotPendingError(
            "Only a pending enterprise plan request can be approved."
        )

    now = timezone.now()

    existing_subscription = (
        ChatbotSubscription.objects.select_for_update()
        .filter(
            chatbot_id=locked_request.chatbot_id,
            status__in=OPEN_SUBSCRIPTION_STATUSES,
        )
        .first()
    )
    if existing_subscription is not None:
        existing_subscription.status = SubscriptionStatus.CANCELED
        existing_subscription.canceled_at = now
        existing_subscription.ended_at = now
        existing_subscription.cancel_at_period_end = False
        existing_subscription.next_billing_at = None
        existing_subscription.updated_by = user
        existing_subscription.save(
            update_fields=[
                "status",
                "canceled_at",
                "ended_at",
                "cancel_at_period_end",
                "next_billing_at",
                "updated_by",
                "updated_at",
            ]
        )

    features = list(features or [])
    plan = SubscriptionPlan(
        name=f"Enterprise — {locked_request.chatbot.chatbot_name}",
        plan_type=PlanType.CUSTOM,
        ai_message_limit=ai_message_limit,
        file_size_limit_mb=file_size_limit_mb,
        knowledge_chunk_limit=knowledge_chunk_limit,
        features=features,
        is_free=False,
        is_public=False,
        requires_sales_contact=True,
        created_by=user,
        updated_by=user,
    )
    plan.full_clean()
    plan.save()

    price = PlanPrice(
        plan=plan,
        provider=PaymentProvider.MANUAL,
        billing_interval=BillingInterval.CUSTOM,
        currency="USD",
        amount=Decimal("0.00"),
        created_by=user,
        updated_by=user,
    )
    price.full_clean()
    price.save()

    subscription = ChatbotSubscription(
        chatbot=locked_request.chatbot,
        plan_price=price,
        selected_by=locked_request.created_by,
        provider=PaymentProvider.MANUAL,
        renewal_mode=RenewalMode.MANUAL,
        status=SubscriptionStatus.ACTIVE,
        started_at=now,
        current_period_start=now,
        current_period_end=expires_at,
        next_billing_at=expires_at,
        created_by=user,
        updated_by=user,
    )
    subscription.full_clean()
    subscription.save()
    _apply_subscription_capacity(subscription)

    locked_request.status = EnterprisePlanRequestStatus.APPROVED
    locked_request.approved_features = features
    locked_request.approved_ai_message_limit = ai_message_limit
    locked_request.approved_file_size_limit_mb = file_size_limit_mb
    locked_request.approved_knowledge_chunk_limit = knowledge_chunk_limit
    locked_request.expires_at = expires_at
    locked_request.subscription = subscription
    locked_request.reviewed_by = user
    locked_request.reviewed_at = now
    locked_request.review_notes = review_notes or ""
    locked_request.updated_by = user
    locked_request.save(
        update_fields=[
            "status",
            "approved_features",
            "approved_ai_message_limit",
            "approved_file_size_limit_mb",
            "approved_knowledge_chunk_limit",
            "expires_at",
            "subscription",
            "reviewed_by",
            "reviewed_at",
            "review_notes",
            "updated_by",
            "updated_at",
        ]
    )
    return locked_request, subscription
