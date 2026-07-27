from __future__ import annotations

from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile, User


@receiver(post_save, sender=User)
def ensure_profile(sender: type[User], instance: User, created: bool, **kwargs: Any) -> None:
    """Every user has a profile from the moment they exist.

    get_or_create rather than create-on-`created`: it keeps fixtures, data
    migrations and manage.py shell sessions from producing profile-less users.
    """
    Profile.objects.get_or_create(user=instance)
