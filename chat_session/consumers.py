import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from chatbot.models import ChatbotUser
from chatbot.utils.choices import ChatbotPermissionTypes
from chat_session.models import ChatSession
from chat_session.services.events import chat_session_group
from chat_session.services.messages import send_agent_message
from chat_session.services.visitor import (
    get_public_chatbot,
    require_allowed_widget_origin,
    send_visitor_message,
)
from chat_session.services.visitor_tokens import (
    InvalidConversationToken,
    decode_conversation_token,
)
from chat_session.tasks import dispatch_ai_reply, is_ai_reply_enabled
from chat_session.utils.choices import ChatSessionStatus


logger = logging.getLogger(__name__)


class ChatSessionConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return

        session_id = self.scope["url_route"]["kwargs"]["session_id"]
        access = await self.get_access(user.id, session_id)
        if access is None:
            await self.close(code=4403)
            return

        self.chat_session, self.agent = access
        self.group_name = chat_session_group(session_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "type": "connection.ready",
                "session_id": str(session_id),
            }
        )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )

    async def receive_json(self, content, **kwargs):
        event_type = content.get("type")
        if event_type == "ping":
            await self.send_json({"type": "pong"})
            return
        if event_type != "message.send":
            await self.send_error("Unsupported event type.")
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

        try:
            message = await database_sync_to_async(send_agent_message)(
                self.chat_session,
                self.agent,
                content=message_content,
                metadata=metadata,
            )
        except ValidationError as exc:
            await self.send_error(next(iter(exc.messages), "Message failed."))
            return
        except ChatSession.DoesNotExist:
            await self.send_error("Chat session is no longer available.")
            await self.close(code=4404)
            return
        await self.send_json(
            {
                "type": "message.accepted",
                "session_id": str(self.chat_session.id),
                "message_id": str(message.id),
            }
        )

    async def chat_session_event(self, event):
        await self.send_json(event["event"])

    async def send_error(self, message):
        await self.send_json({"type": "error", "message": message})

    @database_sync_to_async
    def get_access(self, user_id, session_id):
        try:
            chat_session = ChatSession.objects.select_related(
                "chatbot__workspace"
            ).get(
                pk=session_id,
                chatbot__is_deleted=False,
                chatbot__workspace__is_active=True,
            )
            agent = ChatbotUser.objects.select_related("user", "chatbot").get(
                chatbot=chat_session.chatbot,
                user_id=user_id,
                user__is_active=True,
                is_active=True,
            )
        except (ChatSession.DoesNotExist, ChatbotUser.DoesNotExist):
            return None
        if not agent.has_permission(
            ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT
        ):
            return None
        return chat_session, agent


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
        await self.channel_layer.group_add(self.group_name, self.channel_name)
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

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name,
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
                await self.send_json(
                    {
                        "type": "ai.response.failed",
                        "session_id": str(self.chat_session.id),
                        "data": {"code": "queue_unavailable", "retryable": True},
                    }
                )

    async def chat_session_event(self, event):
        payload = event["event"]
        if payload.get("type") == "message.created":
            payload = {**payload, "data": dict(payload["data"])}
            sender = payload["data"].get("sender")
            if sender:
                payload["data"]["sender"] = {
                    "name": sender.get("name", ""),
                    "avatar": sender.get("avatar", ""),
                }
        elif payload.get("type") in {
            "session.taken_over",
            "session.reassigned",
        }:
            payload = {**payload, "data": {"ai_enabled": False}}
        elif payload.get("type") in {
            "session.released",
            "session.reopened",
        }:
            payload = {**payload, "data": {"ai_enabled": True}}
        elif payload.get("type") in {
            "session.resolved",
            "session.closed",
        }:
            payload = {
                **payload,
                "data": {
                    "ai_enabled": False,
                    "status": payload["data"].get("status"),
                },
            }
        await self.send_json(payload)

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
                visitor_id=payload["visitor_id"],
                status__in=(
                    ChatSessionStatus.ACTIVE,
                    ChatSessionStatus.NEED_ATTENTION,
                ),
            )
        except ChatSession.DoesNotExist:
            return None, 4404
        return session, None
