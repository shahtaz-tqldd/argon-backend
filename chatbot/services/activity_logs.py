from chatbot.models import ChatbotActivityLog
from chatbot.utils.choices import ChatbotActivityModuleTypes


def record_chatbot_activity(
    *,
    chatbot,
    action,
    module=ChatbotActivityModuleTypes.OTHER,
    user=None,
    description="",
    metadata=None,
):
    """Record an action performed by a user (or the system) in a chatbot."""

    activity_log = ChatbotActivityLog(
        chatbot=chatbot,
        user=user,
        action=action,
        module=module,
        description=description,
        metadata={} if metadata is None else metadata,
    )
    activity_log.full_clean(validate_unique=False, validate_constraints=False)
    activity_log.save()
    return activity_log


# A concise alias for callers already operating within chatbot-specific code.
record_activity_log = record_chatbot_activity


def activity_update_metadata(previous, updated):
    """Include snapshots only for values that actually changed."""
    changes = {
        field: {"previous_value": value, "updated_value": updated[field]}
        for field, value in previous.items()
        if value != updated[field]
    }
    return {"updated_fields": sorted(changes), "changes": changes}



def activity_serializer_snapshot(serializer, instance, fields=None):
    """Capture JSON-safe values before serializers mutate an instance."""
    from copy import deepcopy

    data = serializer.to_representation(instance)
    if fields is None:
        fields = [name for name, field in serializer.fields.items() if not field.read_only]
    return {field: deepcopy(data[field]) for field in fields}
