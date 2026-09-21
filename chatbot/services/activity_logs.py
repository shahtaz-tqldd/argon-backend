from chatbot.models import ChatbotActivityLog


def record_chatbot_activity(
    *,
    chatbot,
    action,
    user=None,
    description="",
    metadata=None,
):
    """Record an action performed by a user (or the system) in a chatbot."""

    activity_log = ChatbotActivityLog(
        chatbot=chatbot,
        user=user,
        action=action,
        description=description,
        metadata={} if metadata is None else metadata,
    )
    activity_log.full_clean(validate_unique=False, validate_constraints=False)
    activity_log.save()
    return activity_log


# A concise alias for callers already operating within chatbot-specific code.
record_activity_log = record_chatbot_activity
