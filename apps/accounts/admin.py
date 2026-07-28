from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import LegacySubscriber, Profile, User, UserJournalSubscription


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "specialty", "update_frequency", "onboarding_completed_at")
    list_filter = ("specialty", "update_frequency")
    search_fields = ("user__email", "full_name", "workplace")
    autocomplete_fields = ("user", "specialty")
    readonly_fields = ("created_at", "updated_at", "feed_last_viewed_at")


@admin.register(UserJournalSubscription)
class UserJournalSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "journal", "source", "is_active")
    list_filter = ("is_active", "source", "journal__is_general")
    search_fields = ("user__email", "journal__display_name")
    autocomplete_fields = ("user", "journal")


@admin.register(LegacySubscriber)
class LegacySubscriberAdmin(admin.ModelAdmin):
    """Read-mostly: rows come from import_legacy_subscribers, not by hand."""

    list_display = ("email", "first_name", "specialty_slug", "claimed_by", "imported_at")
    list_filter = ("specialty_slug",)
    search_fields = ("email", "first_name")
    autocomplete_fields = ("claimed_by",)
    readonly_fields = ("imported_at",)
