from uuid import UUID

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from redis.exceptions import RedisError

from base.socket.events import NOTIFICATION_CREATED
from base.socket.services.access import dashboard_access, session_access
from base.socket.services.groups import chat_session_dashboard_group
from base.socket.services.presence import heartbeat
from app.utils.logger import logger
from chat.models import ChatSession
from chat.services.messages import send_agent_message


class DashboardConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.group_names = set()
        self.session_ids = set()
        user = self.scope["user"]
        if not user.is_authenticated or not user.is_active:
            await self.close(code=4401)
            return
        if not await self.refresh_access():
            return
        await self.accept()
        await self.send_json({
            "type": "connection.ready",
            "data": {"heartbeat_interval_seconds": settings.PRESENCE_HEARTBEAT_SECONDS,
                     "presence_timeout_seconds": settings.PRESENCE_TIMEOUT_SECONDS},
        })
        await self.update_presence()

    async def disconnect(self, close_code):
        for group in getattr(self, "group_names", set()):
            await self.channel_layer.group_discard(group, self.channel_name)
        # Another tab may still be alive. Expiry is based on the last heartbeat.

    async def refresh_access(self):
        if not await database_sync_to_async(
            get_user_model().objects.filter(pk=self.scope["user"].pk, is_active=True).exists
        )():
            await self.close(code=4401)
            return False
        groups, self.workspace_ids = await database_sync_to_async(dashboard_access)(
            self.scope["user"].id
        )
        for session_id in self.session_ids.copy():
            if await self.get_session_access(session_id):
                groups.add(chat_session_dashboard_group(session_id))
            else:
                self.session_ids.discard(session_id)
        for group in self.group_names - groups:
            await self.channel_layer.group_discard(group, self.channel_name)
        # Refresh Channels group expiry for long-lived dashboard connections too.
        for group in groups:
            await self.channel_layer.group_add(group, self.channel_name)
        self.group_names = groups
        return True

    async def update_presence(self):
        try:
            snapshots = await sync_to_async(heartbeat, thread_sensitive=False)(
                self.scope["user"].id, self.workspace_ids,
            )
        except RedisError:
            logger.exception("Dashboard presence unavailable")
            await self.send_error("Presence is temporarily unavailable.")
            await self.close(code=1013)
            return False
        for snapshot in snapshots:
            await self.send_json(snapshot)
        return True

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.send_error("Expected a JSON object.")
            return
        event_type = content.get("type")
        if not isinstance(event_type, str):
            await self.send_error("Event type must be a string.")
            return
        if not await self.refresh_access():
            return
        if event_type in {"ping", "presence.heartbeat"}:
            if await self.update_presence():
                await self.send_json({"type": "pong"})
            return
        if event_type not in {"session.subscribe", "session.unsubscribe", "message.send"}:
            await self.send_error("Unsupported event type.")
            return
        try:
            session_id = str(UUID(str(content.get("session_id", ""))))
        except (ValueError, TypeError, AttributeError):
            await self.send_error("A valid session_id is required.")
            return
        if event_type == "session.unsubscribe":
            self.session_ids.discard(session_id)
            group = chat_session_dashboard_group(session_id)
            await self.channel_layer.group_discard(group, self.channel_name)
            self.group_names.discard(group)
            await self.send_json({"type": "session.unsubscribed", "session_id": session_id})
            return
        access = await self.get_session_access(session_id)
        if access is None:
            await self.send_error("Session is unavailable or access is denied.")
            return
        if event_type == "session.subscribe":
            if session_id not in self.session_ids and len(self.session_ids) >= 100:
                await self.send_error("Too many session subscriptions.")
                return
            # Chat events already arrive via the chatbot group. Subscribe only
            # to session-specific notifications, avoiding duplicate chat delivery.
            group = chat_session_dashboard_group(session_id)
            await self.channel_layer.group_add(group, self.channel_name)
            self.group_names.add(group)
            self.session_ids.add(session_id)
            await self.send_json({"type": "session.subscribed", "session_id": session_id})
            return
        message_content = content.get("content")
        metadata = content.get("metadata", {})
        if not isinstance(message_content, str) or not message_content.strip():
            await self.send_error("Message content cannot be blank.")
            return
        if len(message_content) > 10000:
            await self.send_error("Message content cannot exceed 10000 characters.")
            return
        if not isinstance(metadata, dict):
            await self.send_error("Metadata must be a JSON object.")
            return
        session, agent = access
        try:
            message = await database_sync_to_async(send_agent_message)(
                session, agent, content=message_content, metadata=metadata,
            )
        except ValidationError as exc:
            await self.send_error(next(iter(exc.messages), "Message failed."))
            return
        except ChatSession.DoesNotExist:
            await self.send_error("Chat session is no longer available.")
            return
        await self.send_json({"type": "message.accepted", "session_id": session_id,
                              "message_id": str(message.id)})

    async def get_session_access(self, session_id):
        return await database_sync_to_async(session_access)(self.scope["user"].id, session_id)

    async def notification_created(self, event):
        if await self.refresh_access() and event.get("group") in self.group_names:
            await self.send_json({"type": NOTIFICATION_CREATED, "data": event["notification"]})

    async def chat_session_event(self, event):
        # Recheck on delivery, so revoked memberships cannot keep reading chats.
        if await self.get_session_access(event["event"]["session_id"]):
            await self.send_json(event["event"])

    async def dashboard_event(self, event):
        if not await self.refresh_access() or event.get("group") not in self.group_names:
            return
        payload = event["event"]
        if payload.get("session_id") and not await self.get_session_access(payload["session_id"]):
            return
        await self.send_json(payload)

    async def send_error(self, message):
        await self.send_json({"type": "error", "message": message})
