from corsheaders.middleware import CorsMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from chatbot.cors import allow_public_widget_api


class PublicWidgetCORSAPITests(SimpleTestCase):
    public_key = "a" * 40
    visitor_id = "f383644326f34a538906d3fb282be78b"

    def setUp(self):
        self.request_factory = RequestFactory()

    def assert_widget_cors_enabled(self, path):
        request = self.request_factory.get(path)
        self.assertTrue(allow_public_widget_api(None, request))

    def test_visitor_detail_api_enables_widget_cors(self):
        self.assert_widget_cors_enabled(
            f"/api/v1/chatbots/{self.public_key}/"
            f"visitors/{self.visitor_id}/"
        )

    def test_visitor_session_list_api_enables_widget_cors(self):
        self.assert_widget_cors_enabled(
            f"/api/v1/chatbots/{self.public_key}/"
            f"visitors/{self.visitor_id}/sessions/"
        )

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_visitor_api_response_includes_cors_origin_header(self):
        origin = "https://widget.example.com"
        request = self.request_factory.get(
            f"/api/v1/chatbots/{self.public_key}/"
            f"visitors/{self.visitor_id}/sessions/",
            HTTP_ORIGIN=origin,
        )
        middleware = CorsMiddleware(lambda request: HttpResponse())

        response = middleware(request)

        self.assertEqual(response["Access-Control-Allow-Origin"], origin)

    def test_unrelated_chatbot_api_does_not_enable_widget_cors(self):
        request = self.request_factory.get(
            f"/api/v1/chatbots/{self.public_key}/private/"
        )
        self.assertFalse(allow_public_widget_api(None, request))
