import json

from django.conf import settings


_firebase_app = None


class FirebaseVerificationError(ValueError):
    pass


def verify_firebase_id_token(id_token):
    if not settings.FIREBASE_VERIFY_ID_TOKEN:
        return None

    try:
        import firebase_admin
        from firebase_admin import auth, credentials
    except ImportError as exc:
        raise FirebaseVerificationError("firebase-admin is required to verify Firebase ID tokens.") from exc

    global _firebase_app
    if _firebase_app is None:
        if firebase_admin._apps:
            _firebase_app = firebase_admin.get_app()
        elif settings.FIREBASE_SERVICE_ACCOUNT_JSON:
            service_account = json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
            _firebase_app = firebase_admin.initialize_app(credentials.Certificate(service_account))
        else:
            raise FirebaseVerificationError("Firebase service account credentials are not configured.")

    try:
        return auth.verify_id_token(id_token, app=_firebase_app)
    except Exception as exc:
        raise FirebaseVerificationError("Invalid Firebase ID token.") from exc
