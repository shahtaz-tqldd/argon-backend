from django.contrib.auth import get_user_model
from accounts.models import UserProfile

User = get_user_model()

def ensure_user_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile
