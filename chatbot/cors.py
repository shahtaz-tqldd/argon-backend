import re

from corsheaders.signals import check_request_enabled
from django.dispatch import receiver


PUBLIC_WIDGET_API_PATTERN = re.compile(
    r"^/api/v1/chatbots/[A-Za-z0-9_-]{40,64}/(?:"
    r"conversations/(?:[0-9a-fA-F-]{36}/messages/)?"
    r")?$"
)


@receiver(check_request_enabled)
def allow_public_widget_api(sender, request, **kwargs):
    """Let widget APIs handle their own per-chatbot origin authorization."""

    return bool(PUBLIC_WIDGET_API_PATTERN.fullmatch(request.path_info))
