"""Qualification signal recording for captured leads.

Every AI-recorded score becomes an immutable LeadSignal row; the lead's
denormalized avg_score is refreshed from the signal set on each write.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Avg

from lead_capture.models import LeadSignal


def refresh_lead_avg_score(lead):
    """Recompute and persist lead.avg_score from its signals."""
    average = lead.signals.aggregate(average=Avg("score"))["average"]
    lead.avg_score = (
        # Avg() resolves to FloatField for integer sources, but Decimal is
        # required for quantize(); normalize either type.
        Decimal(str(average)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if average is not None
        else None
    )
    lead.save(update_fields=["avg_score", "updated_at"])
    return lead.avg_score


@transaction.atomic
def record_lead_signal(lead, *, score, intent, message=None):
    """Append one qualification signal and refresh the lead's avg_score."""
    signal = LeadSignal(
        lead=lead,
        message=message,
        score=score,
        intent=intent,
    )
    signal.full_clean()
    signal.save()
    # select_for_update on the lead keeps concurrent signals from computing
    # a stale average.
    refreshed_lead = (
        type(lead).objects.select_for_update().get(pk=lead.pk)
    )
    refresh_lead_avg_score(refreshed_lead)
    lead.avg_score = refreshed_lead.avg_score
    return signal
