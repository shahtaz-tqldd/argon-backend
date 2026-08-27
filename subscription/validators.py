from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    PlanFeature,
    PlanType,
)


def _require_mapping(value, key):
    section = value.get(key)
    if not isinstance(section, dict):
        raise ValidationError(f"Snapshot section '{key}' must be an object.")
    return section


def _validate_limit(value, field_name):
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValidationError(
            {field_name: "A snapshot limit must be a non-negative integer or null."}
        )


def _decimal_value(value, field_name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({field_name: "A valid decimal value is required."}) from exc
    if result < 0:
        raise ValidationError({field_name: "The value cannot be negative."})
    return result


def validate_subscription_snapshot(value):
    """Validate the versioned contract copied onto a chatbot subscription."""
    if not isinstance(value, dict):
        raise ValidationError("The subscription snapshot must be an object.")
    if value.get("version") != 1:
        raise ValidationError({"version": "Unsupported subscription snapshot version."})

    plan = _require_mapping(value, "plan")
    pricing = _require_mapping(value, "pricing")
    limits = _require_mapping(value, "limits")
    overage = _require_mapping(value, "overage")

    required_plan_fields = {"id", "name", "type", "is_free", "details_html", "features"}
    missing = required_plan_fields.difference(plan)
    if missing:
        raise ValidationError({"plan": f"Missing fields: {', '.join(sorted(missing))}."})
    if not isinstance(plan["id"], str) or not plan["id"]:
        raise ValidationError({"plan.id": "A plan identifier is required."})
    if not isinstance(plan["name"], str) or not plan["name"]:
        raise ValidationError({"plan.name": "A plan name is required."})
    if plan["type"] not in PlanType.values:
        raise ValidationError({"plan.type": "Unknown plan type."})
    if not isinstance(plan["is_free"], bool):
        raise ValidationError({"plan.is_free": "A boolean value is required."})
    if not isinstance(plan["details_html"], str):
        raise ValidationError({"plan.details_html": "A string value is required."})
    if not isinstance(plan["features"], list):
        raise ValidationError({"plan.features": "Features must be a list."})
    if any(not isinstance(feature, str) for feature in plan["features"]):
        raise ValidationError({"plan.features": "Every feature must be a string."})
    if len(plan["features"]) != len(set(plan["features"])):
        raise ValidationError({"plan.features": "Features must be unique."})
    invalid_features = set(plan["features"]).difference(PlanFeature.values)
    if invalid_features:
        raise ValidationError(
            {"plan.features": f"Unknown features: {', '.join(sorted(invalid_features))}."}
        )

    required_pricing_fields = {
        "plan_price_id",
        "provider",
        "billing_interval",
        "amount",
        "currency",
    }
    missing = required_pricing_fields.difference(pricing)
    if missing:
        raise ValidationError(
            {"pricing": f"Missing fields: {', '.join(sorted(missing))}."}
        )
    if not isinstance(pricing["plan_price_id"], str) or not pricing["plan_price_id"]:
        raise ValidationError({"pricing.plan_price_id": "A price identifier is required."})
    if pricing["provider"] not in PaymentProvider.values:
        raise ValidationError({"pricing.provider": "Unknown payment provider."})
    if pricing["billing_interval"] not in BillingInterval.values:
        raise ValidationError({"pricing.billing_interval": "Unknown billing interval."})
    amount = _decimal_value(pricing["amount"], "pricing.amount")
    if plan["is_free"] and amount != Decimal("0"):
        raise ValidationError({"pricing.amount": "A free plan must have a zero price."})
    if not isinstance(pricing["currency"], str) or len(pricing["currency"]) != 3:
        raise ValidationError({"pricing.currency": "Use a three-letter currency code."})

    for field_name in ("ai_message_limit", "file_size_limit_mb", "knowledge_chunk_limit"):
        if field_name not in limits:
            raise ValidationError({"limits": f"Missing field: {field_name}."})
        _validate_limit(limits[field_name], f"limits.{field_name}")

    if "enabled" not in overage or not isinstance(overage["enabled"], bool):
        raise ValidationError({"overage.enabled": "A boolean value is required."})
    unit_price = overage.get("unit_price")
    if overage["enabled"]:
        if limits["ai_message_limit"] is None:
            raise ValidationError(
                {"limits.ai_message_limit": "An unlimited plan cannot use overage billing."}
            )
        if unit_price is None:
            raise ValidationError({"overage.unit_price": "A unit price is required."})
        _decimal_value(unit_price, "overage.unit_price")
    elif unit_price is not None:
        raise ValidationError(
            {"overage.unit_price": "A disabled overage policy cannot have a unit price."}
        )
