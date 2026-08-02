from django.conf import settings
from django.http import HttpRequest


def initials(full_name: str, email: str) -> str:
    basis = full_name.strip() or email
    words = [w for w in basis.split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return basis[:2].upper()


def site(request: HttpRequest) -> dict[str, str]:
    context = {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_BASE_URL": settings.SITE_BASE_URL,
        "MARKETING_BASE_URL": settings.MARKETING_BASE_URL,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, "profile", None)
        full_name = profile.full_name if profile is not None else ""
        context["user_initials"] = initials(full_name, user.email)
    return context
