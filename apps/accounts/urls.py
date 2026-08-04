from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("onboarding/", views.onboarding_start, name="onboarding_start"),
    path(
        "onboarding/profile/",
        views.profile_step,
        {"is_onboarding": True},
        name="onboarding_profile",
    ),
    path(
        "onboarding/journals/",
        views.journals_step,
        {"is_onboarding": True},
        name="onboarding_journals",
    ),
    path(
        "onboarding/frequency/",
        views.frequency_step,
        {"is_onboarding": True},
        name="onboarding_frequency",
    ),
    path(
        "settings/profile/", views.profile_step, {"is_onboarding": False}, name="settings_profile"
    ),
    path(
        "settings/journals/",
        views.journals_step,
        {"is_onboarding": False},
        name="settings_journals",
    ),
    path(
        "settings/notifications/",
        views.frequency_step,
        {"is_onboarding": False},
        name="settings_notifications",
    ),
    path("account/", views.account, name="account"),
    path(
        "account/resend-verification/",
        views.resend_verification,
        name="resend_verification",
    ),
    path("account/delete/", views.delete_account_confirm, name="delete_account_confirm"),
]
