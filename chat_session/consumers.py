from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import ValidationError

from chatbot.models import ChatbotUser
from chatbot.utils.choices import ChatbotPermissionTypes
from chat_session.models import ChatSession
from chat_session.services.events import chat_session_group
from chat_session.services.messages import send_agent_message


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
