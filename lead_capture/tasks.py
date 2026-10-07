from celery import shared_task

from app.utils.logger import logger
from chatbot.models import Chatbot
from lead_capture.services.insights import (
    generate_weekly_lead_insight,
    last_week_window,
)


@shared_task(name="lead_capture.tasks.generate_weekly_lead_ai_insights")
def generate_weekly_lead_ai_insights():
    """Generate last week's AI lead insight for every active chatbot.

    Scheduled Mondays at 09:30 in the Celery timezone. Chatbots without
    visitor messages in the window are skipped; one chatbot's failure does
    not stop the others.
    """
    week_start, week_end = last_week_window()
    result = {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "generated": [],
        "skipped_no_messages": [],
        "failed": [],
    }
    chatbots = Chatbot.objects.filter(
        is_deleted=False,
        workspace__is_active=True,
    ).order_by("id")
    for chatbot in chatbots.iterator():
        try:
            insight = generate_weekly_lead_insight(
                chatbot,
                week_start=week_start,
                week_end=week_end,
            )
        except Exception:
            logger.exception(
                "Weekly lead AI insight failed chatbot_id=%s", chatbot.id
            )
            result["failed"].append(str(chatbot.slug))
            continue
        if insight is None:
            result["skipped_no_messages"].append(str(chatbot.slug))
        else:
            result["generated"].append(str(chatbot.slug))
    return result
