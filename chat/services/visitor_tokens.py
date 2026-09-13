from django.conf import settings
from django.core import signing


CONVERSATION_TOKEN_SALT = "argon.widget-conversation.v1"


class InvalidConversationToken(Exception):
    pass


def issue_conversation_token(chat_session):
    return signing.dumps(
        {
            "session_id": str(chat_session.id),
            "chatbot_id": str(chat_session.chatbot_id),
            "visitor_id": chat_session.visitor_id,
        },
        salt=CONVERSATION_TOKEN_SALT,
        compress=True,
    )


def decode_conversation_token(token):
    try:
        payload = signing.loads(
            token,
            salt=CONVERSATION_TOKEN_SALT,
            max_age=settings.WIDGET_CONVERSATION_TOKEN_TTL_SECONDS,
        )
    except signing.BadSignature as exc:
        raise InvalidConversationToken(
            "The conversation token is invalid or expired."
        ) from exc

    required_fields = {"session_id", "chatbot_id", "visitor_id"}
    if not isinstance(payload, dict) or not required_fields.issubset(payload):
        raise InvalidConversationToken("The conversation token is invalid.")
    return payload
