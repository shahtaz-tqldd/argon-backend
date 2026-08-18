from django.core.exceptions import ValidationError
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class SafeJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        try:
            return super().get_user(validated_token)
        except (ValidationError, ValueError) as exc:
            raise AuthenticationFailed("Invalid token user identifier.") from exc
