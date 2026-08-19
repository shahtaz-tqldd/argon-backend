import secrets
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError


def generate_widget_public_key():
    """Return a URL-safe public identifier for loading a chatbot widget."""
    return secrets.token_urlsafe(32)


def normalize_widget_origin(value):
    """Validate and normalize an HTTP(S) origin to scheme://host[:port]."""
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid HTTP(S) origin.") from exc

    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError(
            "Enter an origin only, for example https://www.example.com."
        )

    host = parsed.hostname.casefold()
    if ":" in host:
        host = f"[{host}]"

    default_port = (parsed.scheme.casefold() == "http" and port == 80) or (
        parsed.scheme.casefold() == "https" and port == 443
    )
    port_suffix = f":{port}" if port and not default_port else ""
    return f"{parsed.scheme.casefold()}://{host}{port_suffix}"


def validate_widget_settings(value):
    if not isinstance(value, dict):
        raise ValidationError("Widget settings must be a JSON object.")


def validate_other_settings(value):
    if not isinstance(value, dict):
        raise ValidationError("Widget settings must be a JSON object.")

