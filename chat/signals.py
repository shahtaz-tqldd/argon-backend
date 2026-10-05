from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from chat.models import ChatMessage
from chat.services.events import publish_session_event
from chat.services.messages import serialize_message_event


@receiver(post_save, sender=ChatMessage)
def publish_new_chat_message(sender, instance, created, **kwargs):
    if not created:
        return
    session_id = instance.chat_session_id
    chatbot_id = instance.chat_session.chatbot_id
    # Serialize on commit so attachment rows created in the same
    # transaction as the message are part of the published payload.
    transaction.on_commit(
        lambda: publish_session_event(
            session_id,
            chatbot_id,
            "message.created",
            serialize_message_event(instance),
        )
    )
