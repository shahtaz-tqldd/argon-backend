from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from redis.exceptions import RedisError

from app.utils.logger import logger
from base.socket.events import PRESENCE_COUNT
from base.socket.services.groups import chat_session_group, chatbot_widget_group
from base.socket.services.presence import chatbot_online_count
from chat.models import ChatSession
from chat.services.events import publish_session_event
from chat.services.visitor import (
    get_public_chatbot,
    require_allowed_widget_origin,
    send_visitor_message,
)
from chat.services.visitor_tokens import (
    InvalidConversationToken,
    decode_conversation_token,
)
from chat.tasks import dispatch_ai_reply, is_ai_reply_enabled
from chat.utils.choices import ChatSessionStatus


class VisitorChatSessionConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        public_key = self.scope["url_route"]["kwargs"]["public_key"]
        session_id = self.scope["url_route"]["kwargs"]["session_id"]
        query_params = parse_qs(self.scope.get("query_string", b"").decode())
        token = (query_params.get("token") or [""])[0]
        origin = dict(self.scope.get("headers") or []).get(b"origin", b"").decode()
        access, close_code = await self.get_access(
            public_key,
            session_id,
            token,
            origin,
        )
        if access is None:
            await self.close(code=close_code)
            return

        self.chat_session = access
        self.group_name = chat_session_group(session_id)
        self.presence_group_name = chatbot_widget_group(
            self.chat_session.chatbot_id
        )
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.channel_layer.group_add(
            self.presence_group_name,
            self.channel_name,
        )
        await self.accept()
        await self.send_json(
            {
                "type": "connection.ready",
                "session_id": str(session_id),
                "data": {
                    "status": self.chat_session.status,
                    "ai_enabled": is_ai_reply_enabled(self.chat_session),
                },
            }
        )
        try:
            count = await sync_to_async(
                chatbot_online_count,
                thread_sensitive=False,
            )(self.chat_session.chatbot_id)
        except RedisError:
            # Presence is supplementary; a Redis outage must not break chat.
            logger.exception("Widget presence unavailable")
        else:
            await self.send_json(
                {
                    "type": PRESENCE_COUNT,
                    "data": {"online_count": count},
                }
            )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )
        if hasattr(self, "presence_group_name"):
            await self.channel_layer.group_discard(
                self.presence_group_name,
                self.channel_name,
            )

    async def receive_json(self, content, **kwargs):
        event_type = content.get("type")
        if event_type == "ping":
            await self.send_json({"type": "pong"})
            return
        if event_type != "message.send":
            await self.send_error("unsupported_event", "Unsupported event type.")
            return

        message_content = content.get("content")
        metadata = content.get("metadata", {})
        client_message_id = content.get("client_message_id", "")
        if not isinstance(message_content, str) or not message_content.strip():
            await self.send_error(
                "invalid_content",
                "Message content cannot be blank.",
            )
            return
        if len(message_content) > 10000:
            await self.send_error(
                "invalid_content",
                "Message content cannot exceed 10000 characters.",
            )
            return
        if not isinstance(metadata, dict):
            await self.send_error(
                "invalid_metadata",
                "Metadata must be a JSON object.",
            )
            return
        if not isinstance(client_message_id, str) or len(client_message_id) > 255:
            await self.send_error(
                "invalid_client_message_id",
                "client_message_id must be a string up to 255 characters.",
            )
            return

        try:
            message, created = await database_sync_to_async(send_visitor_message)(
                self.chat_session,
                content=message_content,
                metadata=metadata,
                external_id=client_message_id,
            )
        except (ValidationError, ChatSession.DoesNotExist) as exc:
            error_message = (
                next(iter(exc.messages), "Message failed.")
                if isinstance(exc, ValidationError)
                else "Conversation is no longer available."
            )
            await self.send_error("message_rejected", error_message)
            return

        await self.send_json(
            {
                "type": "message.accepted",
                "session_id": str(self.chat_session.id),
                "data": {
                    "message_id": str(message.id),
                    "client_message_id": client_message_id,
                    "duplicate": not created,
                },
            }
        )
        if created:
            try:
                await database_sync_to_async(
                    dispatch_ai_reply
                )(str(message.id))
            except Exception:
                logger.exception(
                    "Could not queue AI reply for visitor message %s",
                    message.id,
                )
                await database_sync_to_async(publish_session_event)(
                    self.chat_session.id,
                    self.chat_session.chatbot_id,
                    "ai.response.failed",
                    {"code": "queue_unavailable", "retryable": True},
                )

    async def chat_session_event(self, event):
        payload = event["event"]
        if payload.get("type") == "message.created":
            visibility = (
                payload.get("data", {}).get("metadata", {}).get("visibility")
            )
            if visibility == "internal":
                return
            payload = {**payload, "data": dict(payload["data"])}
            sender = payload["data"].get("sender")
            if sender:
                payload["data"]["sender"] = {
                    "name": sender.get("name", ""),
                    "avatar": sender.get("avatar", ""),
                }
        elif payload.get("type") in {
            "session.taken_over",
            "session.transferred",
        }:
            payload = {**payload, "data": {"ai_enabled": False}}
        elif payload.get("type") in {
            "session.transfer_requested",
            "session.transfer_declined",
            "session.transfer_cancelled",
        }:
            # Transfer workflow details are internal to chatbot agents.
            return
        elif payload.get("type") in {
            "session.released",
            "session.reopened",
        }:
            payload = {**payload, "data": {"ai_enabled": True}}
        elif payload.get("type") == "session.resolved":
            payload = {
                **payload,
                "data": {
                    "ai_enabled": payload["data"].get("ai_enabled", True),
                    "status": payload["data"].get("status"),
                },
            }
        elif payload.get("type") == "session.closed":
            payload = {
                **payload,
                "data": {
                    "ai_enabled": False,
                    "status": payload["data"].get("status"),
                },
            }
        await self.send_json(payload)

    async def widget_presence_event(self, event):
        """Only an aggregate count is ever published to public widgets."""
        await self.send_json(event["event"])

    async def send_error(self, code, message):
        await self.send_json(
            {
                "type": "error",
                "session_id": str(self.chat_session.id),
                "data": {"code": code, "message": message},
            }
        )

    @database_sync_to_async
    def get_access(self, public_key, session_id, token, origin):
        if not token:
            return None, 4401
        try:
            chatbot = get_public_chatbot(public_key)
            require_allowed_widget_origin(chatbot, origin)
            payload = decode_conversation_token(token)
        except InvalidConversationToken:
            return None, 4401
        except PermissionDenied:
            return None, 4403
        except Http404:
            return None, 4404
        if (
            payload["session_id"] != str(session_id)
            or payload["chatbot_id"] != str(chatbot.id)
        ):
            return None, 4403
        try:
            session = ChatSession.objects.select_related("chatbot").get(
                pk=session_id,
                chatbot=chatbot,
                is_test=False,
                visitor_id=payload["visitor_id"],
                status=ChatSessionStatus.OPEN,
            )
        except ChatSession.DoesNotExist:
            return None, 4404
        return session, None
