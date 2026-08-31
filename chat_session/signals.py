from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from chat_session.models import ChatMessage
from chat_session.services.events import publish_session_event
from chat_session.services.messages import serialize_message_event


@receiver(post_save, sender=ChatMessage)
def publish_new_chat_message(sender, instance, created, **kwargs):
    if not created:
        return
    event_data = serialize_message_event(instance)
    transaction.on_commit(
        lambda: publish_session_event(
            instance.chat_session_id,
            "message.created",
            event_data,
        )
    )
