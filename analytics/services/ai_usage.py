from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError

from analytics.models import AIUsage


AI_USAGE_COST_QUANTUM = Decimal("0.00000001")


def _normalize_cost(cost):
    try:
        return Decimal(str(cost)).quantize(
            AI_USAGE_COST_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"cost": ["Enter a valid cost."]}) from exc


def record_ai_usage(
    *,
    user,
    usage_type,
    cost,
    tokens,
    trip=None,
    metadata=None,
):
    usage = AIUsage(
        user=user,
        trip=trip,
        usage_type=usage_type,
        cost=_normalize_cost(cost),
        tokens=tokens,
        metadata={} if metadata is None else metadata,
    )
    usage.full_clean(validate_unique=False, validate_constraints=False)
    usage.save()
    return usage
