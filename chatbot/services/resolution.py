from chatbot.models import Chatbot


def resolve_chatbot_reference(
    chatbot=None,
    *,
    chatbot_slug=None,
    chatbot_id=None,
):
    references = (
        chatbot is not None,
        chatbot_slug is not None,
        chatbot_id is not None,
    )
    if sum(references) != 1:
        raise ValueError(
            "Provide exactly one of chatbot, chatbot_slug, or chatbot_id."
        )

    if chatbot is not None:
        if not isinstance(chatbot, Chatbot):
            raise TypeError("chatbot must be a Chatbot instance.")
        if chatbot.is_deleted:
            raise Chatbot.DoesNotExist("The chatbot does not exist.")
        return chatbot

    filters = {"is_deleted": False}
    if chatbot_slug is not None:
        filters["slug"] = chatbot_slug
    else:
        filters["id"] = chatbot_id

    return Chatbot.objects.only("id", "slug", "is_deleted").get(**filters)
