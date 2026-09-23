import json

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings

from base.socket.consumers.dashboard import DashboardConsumer
from base.socket.consumers.widget import VisitorChatSessionConsumer
from base.socket.services.broadcaster import publish_session_event
from base.socket.services.groups import chatbot_dashboard_group, chat_session_dashboard_group


class DashboardConsumerTests(SimpleTestCase):
    def setUp(self):
        self.session_id = str(uuid4())
        self.consumer = DashboardConsumer()
        self.consumer.scope = {"user": SimpleNamespace(id=uuid4())}
        self.consumer.send_json = AsyncMock()
        self.consumer.close = AsyncMock()
        self.consumer.refresh_access = AsyncMock(return_value=True)
        self.consumer.get_session_access = AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace()))
        self.consumer.group_names = set()
        self.consumer.session_ids = set()
        self.consumer.channel_layer = SimpleNamespace(group_add=AsyncMock(), group_discard=AsyncMock())
        self.consumer.channel_name = "test.channel"

    def test_anonymous_connection_is_rejected(self):
        self.consumer.scope["user"] = AnonymousUser()
        async_to_sync(self.consumer.connect)()
        self.consumer.close.assert_awaited_once_with(code=4401)

    def test_cannot_subscribe_or_send_to_unauthorized_session(self):
        self.consumer.get_session_access.return_value = None
        for event_type in ["session.subscribe", "message.send"]:
            async_to_sync(self.consumer.receive_json)({"type": event_type, "session_id": self.session_id})
            self.assertEqual(self.consumer.send_json.call_args.args[0]["type"], "error")
        self.consumer.channel_layer.group_add.assert_not_awaited()

    def test_subscription_joins_only_session_notification_group(self):
        async_to_sync(self.consumer.receive_json)({"type": "session.subscribe", "session_id": self.session_id})
        self.consumer.channel_layer.group_add.assert_awaited_once_with(
            chat_session_dashboard_group(self.session_id), "test.channel",
        )
        async_to_sync(self.consumer.receive_json)({"type": "session.unsubscribe", "session_id": self.session_id})
        self.assertFalse(self.consumer.session_ids)

    def test_malformed_commands_return_errors(self):
        for content in [[], None, {"type": []}, {"type": {}}, {"type": "session.subscribe", "session_id": "bad"},
                        {"type": "workspace.subscribe", "workspace_id": str(uuid4())}]:
            async_to_sync(self.consumer.receive_json)(content)
            self.assertEqual(self.consumer.send_json.call_args.args[0]["type"], "error")

    def test_revoked_access_drops_chat_event(self):
        self.consumer.get_session_access.return_value = None
        async_to_sync(self.consumer.chat_session_event)({"event": {
            "type": "message.created", "session_id": self.session_id, "data": {},
        }})
        self.consumer.send_json.assert_not_awaited()

    def test_notification_uses_type_data_envelope(self):
        self.consumer.group_names.add("notifications.global")
        async_to_sync(self.consumer.notification_created)({
            "group": "notifications.global", "notification": {"id": "notification-id"},
        })
        self.consumer.send_json.assert_awaited_once_with({
            "type": "notification.created", "data": {"id": "notification-id"},
        })

    @patch("base.socket.consumers.dashboard.send_agent_message")
    def test_send_reuses_domain_service_and_returns_acceptance(self, send):
        send.return_value = SimpleNamespace(id=uuid4())
        async_to_sync(self.consumer.receive_json)({
            "type": "message.send", "session_id": self.session_id, "content": "Hello",
        })
        send.assert_called_once()
        self.assertEqual(self.consumer.send_json.call_args.args[0]["type"], "message.accepted")

    def test_disconnect_does_not_clear_shared_user_presence(self):
        self.consumer.group_names = {"notifications.global"}
        async_to_sync(self.consumer.disconnect)(1000)
        self.consumer.channel_layer.group_discard.assert_awaited_once()

    @patch("base.socket.consumers.dashboard.dashboard_access")
    @patch("base.socket.consumers.dashboard.get_user_model")
    def test_refresh_discards_revoked_groups_and_session_subscriptions(self, user_model, access):
        self.consumer.scope["user"].pk = self.consumer.scope["user"].id
        user_model.return_value.objects.filter.return_value.exists.return_value = True
        access.return_value = ({"notifications.global"}, set(), {})
        self.consumer.group_names = {"notifications.global", "notifications.workspace.revoked"}
        self.consumer.session_ids = {self.session_id}
        self.consumer.get_session_access.return_value = None
        result = async_to_sync(DashboardConsumer.refresh_access)(self.consumer)
        self.assertTrue(result)
        self.assertEqual(self.consumer.group_names, {"notifications.global"})
        self.assertFalse(self.consumer.session_ids)
        self.consumer.channel_layer.group_discard.assert_awaited_once_with(
            "notifications.workspace.revoked", "test.channel",
        )

    def test_notification_outside_current_groups_is_dropped(self):
        async_to_sync(self.consumer.notification_created)({
            "group": "notifications.workspace.other", "notification": {"id": "private"},
        })
        self.consumer.send_json.assert_not_awaited()

    def test_subscription_limit_is_enforced(self):
        self.consumer.session_ids = {str(uuid4()) for _ in range(100)}
        async_to_sync(self.consumer.receive_json)({
            "type": "session.subscribe", "session_id": self.session_id,
        })
        self.consumer.channel_layer.group_add.assert_not_awaited()
        self.assertEqual(self.consumer.send_json.call_args.args[0]["type"], "error")


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class DashboardDeliveryTests(SimpleTestCase):
    def test_one_socket_receives_notifications_and_chat_without_duplicates(self):
        async_to_sync(self.exercise_connection)()

    async def exercise_connection(self):
        session_id, chatbot_id = str(uuid4()), str(uuid4())
        group = chatbot_dashboard_group(chatbot_id)

        async def refresh(consumer):
            consumer.group_names = {group}
            consumer.workspace_ids = set()
            await consumer.channel_layer.group_add(group, consumer.channel_name)
            return True

        with patch.object(DashboardConsumer, "refresh_access", refresh), patch.object(
            DashboardConsumer, "update_presence", AsyncMock(return_value=True)
        ), patch.object(DashboardConsumer, "get_session_access", AsyncMock(return_value=True)):
            client = ApplicationCommunicator(DashboardConsumer.as_asgi(), {
                "type": "websocket", "path": "/ws/dashboard/", "headers": [],
                "user": SimpleNamespace(id=uuid4(), is_authenticated=True, is_active=True),
            })
            try:
                await client.send_input({"type": "websocket.connect"})
                self.assertEqual((await client.receive_output())["type"], "websocket.accept")
                self.assertIn("connection.ready", (await client.receive_output())["text"])
                await get_channel_layer().group_send(group, {
                    "type": "notification.created", "group": group, "notification": {"id": "n1"},
                })
                self.assertIn("notification.created", (await client.receive_output())["text"])
                await client.send_input({"type": "websocket.receive", "text": json.dumps({
                    "type": "session.subscribe", "session_id": session_id,
                })})
                self.assertIn("session.subscribed", (await client.receive_output())["text"])
                await sync_to_async(publish_session_event)(
                    session_id, chatbot_id, "message.created", {"id": "m1"},
                )
                self.assertIn("message.created", (await client.receive_output())["text"])
                self.assertTrue(await client.receive_nothing(timeout=0.02))
                await client.send_input({"type": "websocket.disconnect", "code": 1000})
                await client.wait()
            finally:
                client.stop()

    def test_chatbot_management_update_reaches_every_connected_member(self):
        event = {
            "event": {
                "type": "session.updated",
                "session_id": str(uuid4()),
                "data": {
                    "change": "session.taken_over",
                    "assigned_to": {"id": "agent-id", "name": "Agent"},
                    "has_pending_transfer": False,
                    "transfer_requested_to": None,
                },
            },
        }
        consumers = [DashboardConsumer(), DashboardConsumer()]
        for consumer in consumers:
            consumer.get_session_access = AsyncMock(return_value=True)
            consumer.send_json = AsyncMock()
            async_to_sync(consumer.chat_session_event)(event)
            consumer.send_json.assert_awaited_once_with(event["event"])

    @patch("base.socket.services.broadcaster.broadcast")
    def test_generic_session_update_is_dashboard_only(self, broadcast):
        session_id, chatbot_id = uuid4(), uuid4()
        publish_session_event(session_id, chatbot_id, "session.taken_over", {"agent_id": "agent"})
        groups, payload = broadcast.call_args.args
        self.assertEqual(groups, [chatbot_dashboard_group(chatbot_id)])
        self.assertEqual(payload["event"]["type"], "session.updated")


class WidgetPreservationTests(SimpleTestCase):
    def test_widget_presence_event_exposes_only_the_count(self):
        consumer = VisitorChatSessionConsumer()
        consumer.send_json = AsyncMock()
        async_to_sync(consumer.widget_presence_event)({
            "event": {
                "type": "presence.count",
                "data": {"online_count": 2},
            },
        })
        consumer.send_json.assert_awaited_once_with({
            "type": "presence.count",
            "data": {"online_count": 2},
        })

    def test_widget_sanitizes_sender_identity_and_hides_internal_transfer_events(self):
        consumer = VisitorChatSessionConsumer()
        consumer.send_json = AsyncMock()
        event = {"event": {"type": "message.created", "data": {"sender": {
            "id": "agent", "user_id": "user", "email": "private@example.com",
            "name": "Agent", "avatar": "avatar-url",
        }}}}
        async_to_sync(consumer.chat_session_event)(event)
        sender = consumer.send_json.call_args.args[0]["data"]["sender"]
        self.assertEqual(sender, {"name": "Agent", "avatar": "avatar-url"})
        self.assertIn("email", event["event"]["data"]["sender"])
        consumer.send_json.reset_mock()
        for event_type in ["session.transfer_requested", "session.transfer_declined", "session.transfer_cancelled"]:
            async_to_sync(consumer.chat_session_event)({"event": {"type": event_type}})
        consumer.send_json.assert_not_awaited()

    def test_widget_hides_internal_system_messages(self):
        consumer = VisitorChatSessionConsumer()
        consumer.send_json = AsyncMock()
        event = {
            "event": {
                "type": "message.created",
                "data": {
                    "sender_type": "system",
                    "content": "Conversation transferred from A to B.",
                    "metadata": {"visibility": "internal"},
                },
            }
        }

        async_to_sync(consumer.chat_session_event)(event)

        consumer.send_json.assert_not_awaited()

    def test_widget_keeps_resolved_conversation_open(self):
        consumer = VisitorChatSessionConsumer()
        consumer.send_json = AsyncMock()
        event = {
            "event": {
                "type": "session.resolved",
                "data": {"ai_enabled": True, "status": "open"},
            }
        }

        async_to_sync(consumer.chat_session_event)(event)

        consumer.send_json.assert_awaited_once_with(event["event"])

    @patch("base.socket.consumers.widget.decode_conversation_token")
    @patch("base.socket.consumers.widget.require_allowed_widget_origin")
    @patch("base.socket.consumers.widget.get_public_chatbot")
    def test_widget_rejects_token_bound_to_another_conversation(self, chatbot, origin, decode):
        chatbot.return_value = SimpleNamespace(id=uuid4())
        decode.return_value = {"session_id": str(uuid4()), "chatbot_id": str(chatbot.return_value.id)}
        result = async_to_sync(VisitorChatSessionConsumer().get_access)("key", uuid4(), "token", "https://example.com")
        self.assertEqual(result, (None, 4403))
