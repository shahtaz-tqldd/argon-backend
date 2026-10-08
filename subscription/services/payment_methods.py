from django.db import transaction

from subscription.utils.choices import PaymentProvider
from subscription.models import BillingPaymentMethod
from subscription.services.stripe import stripe_expandable_id


def card_payment_method_defaults(payment_method, *, customer_id, is_default=False):
    card = payment_method.get("card") or {}
    return {
        "provider_customer_id": customer_id,
        "card_brand": card.get("brand") or "",
        "card_last4": card.get("last4") or "",
        "card_exp_month": card.get("exp_month"),
        "card_exp_year": card.get("exp_year"),
        "is_default": is_default,
        "is_active": True,
    }


@transaction.atomic
def sync_payment_method(
    *, chatbot, payment_method, customer_id, is_default=False, updated_by=None
):
    payment_method_id = payment_method.get("id") or ""
    if not payment_method_id or payment_method.get("type") not in {None, "card"}:
        return None

    if is_default:
        BillingPaymentMethod.objects.filter(
            chatbot=chatbot,
            provider=PaymentProvider.STRIPE,
            is_default=True,
        ).exclude(provider_payment_method_id=payment_method_id).update(
            is_default=False
        )

    defaults = card_payment_method_defaults(
        payment_method,
        customer_id=customer_id,
        is_default=is_default,
    )
    defaults["updated_by"] = updated_by
    payment_method_record, _ = BillingPaymentMethod.objects.update_or_create(
        provider=PaymentProvider.STRIPE,
        provider_payment_method_id=payment_method_id,
        defaults={"chatbot": chatbot, **defaults},
    )
    return payment_method_record


def synchronize_customer_payment_methods(*, subscription, stripe_service, user=None):
    customer_id = subscription.provider_customer_id
    customer = stripe_service.retrieve_customer(customer_id=customer_id)
    customer_default_id = stripe_expandable_id(
        (customer.get("invoice_settings") or {}).get("default_payment_method")
    )
    subscription_default_id = ""
    if subscription.provider_subscription_id:
        stripe_subscription = stripe_service.retrieve_subscription(
            subscription_id=subscription.provider_subscription_id
        )
        subscription_default_id = stripe_expandable_id(
            stripe_subscription.get("default_payment_method")
        )
    default_id = subscription_default_id or customer_default_id
    methods = stripe_service.list_card_payment_methods(customer_id=customer_id)
    if default_id:
        BillingPaymentMethod.objects.filter(
            chatbot=subscription.chatbot,
            provider=PaymentProvider.STRIPE,
            is_default=True,
        ).exclude(provider_payment_method_id=default_id).update(
            is_default=False
        )
    provider_ids = []
    records = []
    for method in methods:
        method = dict(method)
        method_id = method.get("id") or ""
        if not method_id:
            continue
        provider_ids.append(method_id)
        records.append(
            sync_payment_method(
                chatbot=subscription.chatbot,
                payment_method=method,
                customer_id=customer_id,
                is_default=method_id == default_id,
                updated_by=user,
            )
        )

    BillingPaymentMethod.objects.filter(
        chatbot=subscription.chatbot,
        provider=PaymentProvider.STRIPE,
        provider_customer_id=customer_id,
    ).exclude(provider_payment_method_id__in=provider_ids).update(
        is_active=False,
        is_default=False,
    )
    return [record for record in records if record is not None]


def deactivate_payment_method(*, provider_payment_method_id):
    BillingPaymentMethod.objects.filter(
        provider=PaymentProvider.STRIPE,
        provider_payment_method_id=provider_payment_method_id,
    ).update(is_active=False, is_default=False)
