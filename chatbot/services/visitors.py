"""Resolution and profile management for chatbot widget visitors.

All visitor_id (cookie) lookups funnel through this module so callers
never deal with the denormalized identity columns by hand.
"""

from django.core.validators import validate_ipv46_address
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from chatbot.models import ChatbotVisitor


def get_visitor(chatbot, visitor_id):
    """Return the chatbot's visitor for a cookie ID, or None."""
    if not visitor_id:
        return None
    return (
        ChatbotVisitor.objects.select_related("lead")
        .filter(chatbot=chatbot, visitor_id=visitor_id)
        .first()
    )


def get_visitor_by_pk(chatbot, visitor_pk):
    """Return the chatbot's visitor for its primary key, or None.

    Conversation tokens carry the visitor PK, so token-authenticated
    lookups resolve through this instead of the cookie ID.
    """
    if not visitor_pk:
        return None
    return (
        ChatbotVisitor.objects.select_related("lead")
        .filter(chatbot=chatbot, pk=visitor_pk)
        .first()
    )


def get_or_create_visitor(chatbot, visitor_id):
    """Return the visitor for a cookie ID, creating it on first contact.

    Uniqueness is enforced by unique_visitor_per_chatbot, so concurrent
    widget boots collapse into a single row.
    """
    visitor, _created = ChatbotVisitor.objects.get_or_create(
        chatbot=chatbot,
        visitor_id=visitor_id,
    )
    return visitor


def _clean_ip_address(ip_address):
    value = str(ip_address or "").strip()
    if not value:
        return None
    try:
        validate_ipv46_address(value)
    except DjangoValidationError:
        return None
    return value


def update_visitor_profile(
    visitor,
    *,
    ip_address=None,
    detected_location=None,
    detected_country=None,
    metadata=None,
):
    """Enrich a visitor's identity columns and merge metadata in place.

    Only truthy values are applied, so callers can pass sparse request
    data without erasing previously captured context. New metadata keys
    override existing ones; unrelated keys are preserved. Saves nothing
    (and costs no query) when nothing changes.
    """
    changed = False

    if ip_address is not None:
        cleaned = _clean_ip_address(ip_address)
        if cleaned and cleaned != visitor.ip_address:
            visitor.ip_address = cleaned
            changed = True

    for field_name, value in (
        ("detected_location", detected_location),
        ("detected_country", detected_country),
    ):
        if value is None:
            continue
        value = str(value).strip()
        if value and value != getattr(visitor, field_name):
            setattr(visitor, field_name, value)
            changed = True

    if metadata is not None:
        if not isinstance(metadata, dict):
            raise DjangoValidationError(
                {"metadata": "Visitor metadata must be a JSON object."}
            )
        merged = {**(visitor.metadata or {}), **metadata}
        if merged != (visitor.metadata or {}):
            visitor.metadata = merged
            changed = True

    if changed:
        visitor.save(
            update_fields=[
                "ip_address",
                "detected_location",
                "detected_country",
                "metadata",
                "updated_at",
            ]
        )
    return visitor


def block_visitor(visitor, *, blocked_by=None):
    """Block a visitor. Returns True when this call changed state.

    Idempotent: re-blocking keeps the original blocked_at/blocked_by.
    """
    if visitor.is_blocked:
        return False
    visitor.is_blocked = True
    visitor.blocked_at = timezone.now()
    visitor.blocked_by = blocked_by
    visitor.save(
        update_fields=[
            "is_blocked",
            "blocked_at",
            "blocked_by",
            "updated_at",
        ]
    )
    return True


def unblock_visitor(visitor):
    """Unblock a visitor. Returns True when this call changed state."""
    if not visitor.is_blocked:
        return False
    visitor.is_blocked = False
    visitor.blocked_at = None
    visitor.blocked_by = None
    visitor.save(
        update_fields=[
            "is_blocked",
            "blocked_at",
            "blocked_by",
            "updated_at",
        ]
    )
    return True
