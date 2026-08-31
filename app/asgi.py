import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings')

django_asgi_app = get_asgi_application()

from notification.middleware import JWTAuthMiddlewareStack
from chat_session.routing import websocket_urlpatterns as chat_websocket_urls
from notification.routing import websocket_urlpatterns as notification_websocket_urls

websocket_urlpatterns = [
    *notification_websocket_urls,
    *chat_websocket_urls,
]

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
