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

    def test_chatbot_config_api_enables_widget_cors(self):
        self.assert_widget_cors_enabled(
            f"/api/v1/chatbots/{self.public_key}/config/"
        )

    def test_visitor_detail_api_enables_widget_cors(self):
        self.assert_widget_cors_enabled(
            f"/api/v1/chatbots/{self.public_key}/visitor/details/"
            f"?visitor_id={self.visitor_id}"
        )

    def test_visitor_session_list_api_enables_widget_cors(self):
        self.assert_widget_cors_enabled(
            f"/api/v1/chatbots/{self.public_key}/sessions/list/"
            f"?visitor_id={self.visitor_id}"
        )

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_visitor_api_response_includes_cors_origin_header(self):
        origin = "https://widget.example.com"
        request = self.request_factory.get(
            f"/api/v1/chatbots/{self.public_key}/sessions/list/"
            f"?visitor_id={self.visitor_id}",
            HTTP_ORIGIN=origin,
        )
        middleware = CorsMiddleware(lambda request: HttpResponse())

        response = middleware(request)

        self.assertEqual(response["Access-Control-Allow-Origin"], origin)

    def test_visitor_appointment_api_enables_widget_cors(self):
        session_id = "d22726eb-533f-4608-bef7-22159e517b0d"
        self.assert_widget_cors_enabled(
            f"/api/v1/chatbots/{self.public_key}/book-appointment/"
            f"?session_id={session_id}"
        )

    def test_unrelated_chatbot_api_does_not_enable_widget_cors(self):
        request = self.request_factory.get(
            f"/api/v1/chatbots/{self.public_key}/private/"
        )
        self.assertFalse(allow_public_widget_api(None, request))
