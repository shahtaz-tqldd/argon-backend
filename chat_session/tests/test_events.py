from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.layers import InMemoryChannelLayer
from django.test import SimpleTestCase

from chat_session.signals import publish_new_chat_message
from chat_session.services.events import chat_session_group, publish_session_event
from chat_session.services.visitor import create_or_resume_conversation
from notification.consumers import NotificationConsumer
from notification.services import chatbot_dashboard_group


class ChatSessionEventTests(SimpleTestCase):
    @patch("chat_session.services.visitor._resolve_conversation_lead")
    @patch("chat_session.services.visitor.ChatSession.objects.create")
    @patch("chat_session.services.visitor.publish_session_event")
    @patch("chat_session.services.visitor.transaction.on_commit")
    def test_new_conversation_publishes_session_created_after_commit(
        self,
        on_commit,
        publish_event,
        create_session,
        resolve_lead,
    ):
        chatbot_id = uuid4()
        session_id = uuid4()
        chatbot = SimpleNamespace(id=chatbot_id, ai_enabled=True)
        session = SimpleNamespace(
            id=session_id,
            channel="web_widget",
            status="open",
        )
        create_session.return_value = session
        resolve_lead.return_value = None
        on_commit.side_effect = lambda callback: callback()

        result, resumed = create_or_resume_conversation.__wrapped__(chatbot)

        self.assertIs(result, session)
        self.assertFalse(resumed)
        publish_event.assert_called_once_with(
            session_id,
            chatbot_id,
            "session.created",
            {
                "chatbot_id": str(chatbot_id),
                "channel": "web_widget",
                "status": "open",
            },
        )

    def test_event_is_published_to_session_and_chatbot_dashboard_groups(self):
        session_id = uuid4()
        chatbot_id = uuid4()
        session_channel = "test.session"
        dashboard_channel = "test.dashboard"
        channel_layer = InMemoryChannelLayer()
        async_to_sync(channel_layer.group_add)(
            chat_session_group(session_id),
            session_channel,
        )
        async_to_sync(channel_layer.group_add)(
            chatbot_dashboard_group(chatbot_id),
            dashboard_channel,
        )

        with patch(
            "chat_session.services.events.get_channel_layer",
            return_value=channel_layer,
        ):
            publish_session_event(
                session_id,
                chatbot_id,
                "message.created",
                {"id": "message-id", "sender_type": "visitor"},
            )

        session_event = async_to_sync(channel_layer.receive)(session_channel)
        dashboard_event = async_to_sync(channel_layer.receive)(dashboard_channel)
        self.assertEqual(session_event, dashboard_event)
        self.assertEqual(session_event["type"], "chat_session.event")
        self.assertEqual(
            session_event["event"],
            {
                "type": "message.created",
                "session_id": str(session_id),
                "data": {"id": "message-id", "sender_type": "visitor"},
            },
        )

    def test_notification_consumer_forwards_chat_session_event(self):
        payload = {
            "type": "message.created",
            "session_id": str(uuid4()),
            "data": {"id": "message-id"},
        }
        consumer = NotificationConsumer()
        consumer.send_json = AsyncMock()

        async_to_sync(consumer.chat_session_event)({"event": payload})

        consumer.send_json.assert_awaited_once_with(payload)

    @patch("chat_session.signals.serialize_message_event")
    @patch("chat_session.signals.publish_session_event")
    @patch("chat_session.signals.transaction.on_commit")
    def test_every_new_message_uses_the_shared_event_publisher(
        self,
        on_commit,
        publish_event,
        serialize_event,
    ):
        session_id = uuid4()
        chatbot_id = uuid4()
        message = SimpleNamespace(
            chat_session_id=session_id,
            chat_session=SimpleNamespace(chatbot_id=chatbot_id),
        )
        event_data = {"id": "message-id", "sender_type": "ai"}
        serialize_event.return_value = event_data
        on_commit.side_effect = lambda callback: callback()

        publish_new_chat_message(None, message, True)

        publish_event.assert_called_once_with(
            session_id,
            chatbot_id,
            "message.created",
            event_data,
        )
