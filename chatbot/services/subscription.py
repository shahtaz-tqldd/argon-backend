from uuid import UUID

from chatbot.services.resolution import resolve_chatbot_reference
from subscription.choices import PlanFeature, SubscriptionStatus
from subscription.models import ChatbotSubscription


class ActiveChatbotSubscriptionNotFound(LookupError):
    """Raised when a chatbot has no active subscription contract."""


class ChatbotSubscriptionEntitlements(dict):
    """JSON-ready entitlements with convenient attribute access."""

    def __init__(
        self,
        *,
        subscription_id,
        chatbot_id,
        ai_message_limit,
        file_size_limit_mb,
        knowledge_chunk_limit,
        features,
    ):
        normalized_features = tuple(PlanFeature(feature) for feature in features)
        super().__init__(
            subscription_id=str(subscription_id),
            chatbot_id=str(chatbot_id),
            ai_message_limit=ai_message_limit,
            file_size_limit_mb=file_size_limit_mb,
            knowledge_chunk_limit=knowledge_chunk_limit,
            features=[feature.value for feature in normalized_features],
        )

    @property
    def subscription_id(self):
        return UUID(self["subscription_id"])

    @property
    def chatbot_id(self):
        return UUID(self["chatbot_id"])

    @property
    def ai_message_limit(self):
        return self["ai_message_limit"]

    @property
    def file_size_limit_mb(self):
        return self["file_size_limit_mb"]

    @property
    def knowledge_chunk_limit(self):
        return self["knowledge_chunk_limit"]

    @property
    def features(self):
        return tuple(PlanFeature(feature) for feature in self["features"])

    def has_feature(self, feature):
        try:
            feature = PlanFeature(feature)
        except ValueError:
            return False
        return feature in self.features


def get_chatbot_subscription_entitlements(
    chatbot=None,
    *,
    chatbot_slug=None,
    chatbot_id=None,
):
    """Return the active, snapshotted subscription limits for one chatbot.

    Exactly one chatbot reference is required. ``None`` limits mean unlimited.
    Features are returned as ``PlanFeature`` enum members.
    """

    chatbot = resolve_chatbot_reference(
        chatbot=chatbot,
        chatbot_slug=chatbot_slug,
        chatbot_id=chatbot_id,
    )

    try:
        subscription = (
            ChatbotSubscription.objects.only("id", "chatbot_id", "snapshot")
            .get(
                chatbot_id=chatbot.id,
                status=SubscriptionStatus.ACTIVE,
            )
        )
    except ChatbotSubscription.DoesNotExist as exc:
        raise ActiveChatbotSubscriptionNotFound(
            f"Chatbot {chatbot.id} has no active subscription."
        ) from exc

    return ChatbotSubscriptionEntitlements(
        subscription_id=subscription.id,
        chatbot_id=subscription.chatbot_id,
        ai_message_limit=subscription.get_ai_message_limit(),
        file_size_limit_mb=subscription.get_file_size_limit_mb(),
        knowledge_chunk_limit=subscription.get_knowledge_chunk_limit(),
        features=subscription.get_features(),
    )
