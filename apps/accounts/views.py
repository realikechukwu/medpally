"""The three onboarding steps, and the settings pages that reuse them.

Each view takes `is_onboarding` to decide where "next" points — the next
wizard step during onboarding, back to the settings page itself otherwise —
which is the entire cost of getting three settings pages for free.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from allauth.account.models import EmailAddress

from apps.catalog.models import Journal, SpecialtyJournal
from apps.common.context_processors import initials

from . import services
from .forms import FrequencyForm, JournalsForm, ProfileForm


@login_required
def onboarding_start(request: HttpRequest) -> HttpResponse:
    return redirect(services.next_onboarding_url_name(request.user))


@login_required
def profile_step(request: HttpRequest, is_onboarding: bool = True) -> HttpResponse:
    profile = request.user.profile
    if is_onboarding and not profile.full_name and not profile.specialty_id:
        services.prefill_from_legacy_subscriber(request.user, profile)

    if request.method == "POST":
        # Captured before is_valid(): Django's ModelForm._post_clean() already
        # writes cleaned data onto `instance` (this same `profile` object)
        # during validation, well before save() is called.
        specialty_before = profile.specialty_id
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            if profile.specialty_id and profile.specialty_id != specialty_before:
                services.apply_specialty_preset(request.user, profile.specialty)

            if is_onboarding:
                return redirect("accounts:onboarding_journals")
            messages.success(request, "Profile updated.")
            return redirect("accounts:settings_profile")
    else:
        form = ProfileForm(instance=profile)

    return render(
        request,
        "accounts/profile_form.html",
        {"form": form, "is_onboarding": is_onboarding, "step": 1},
    )


def _grouped_journals(specialty) -> dict[str, list[Journal]]:
    """Your specialty / General medical / Other specialties, in that order."""
    all_journals = list(Journal.objects.filter(is_active=True).order_by("display_name"))

    your_specialty_ids: set[int] = set()
    if specialty is not None:
        your_specialty_ids = set(
            SpecialtyJournal.objects.filter(specialty=specialty).values_list(
                "journal_id", flat=True
            )
        )

    your_specialty = [j for j in all_journals if j.id in your_specialty_ids]
    general = [j for j in all_journals if j.id not in your_specialty_ids and j.is_general]
    other = [j for j in all_journals if j.id not in your_specialty_ids and not j.is_general]
    return {
        "Your specialty": your_specialty,
        "General medical": general,
        "Other specialties": other,
    }


@login_required
def journals_step(request: HttpRequest, is_onboarding: bool = True) -> HttpResponse:
    profile = request.user.profile
    active_ids = set(
        request.user.journal_subscriptions.filter(is_active=True).values_list(
            "journal_id", flat=True
        )
    )

    if request.method == "POST":
        form = JournalsForm(request.POST)
        if form.is_valid():
            selected_ids = {j.id for j in form.cleaned_data["journals"]}
            services.apply_journal_selection(request.user, selected_ids)

            if is_onboarding:
                return redirect("accounts:onboarding_frequency")
            messages.success(request, "Journals updated.")
            return redirect("accounts:settings_journals")
    else:
        form = JournalsForm(initial={"journals": active_ids})

    return render(
        request,
        "accounts/journals_form.html",
        {
            "form": form,
            "is_onboarding": is_onboarding,
            "step": 2,
            "groups": _grouped_journals(profile.specialty),
            "active_ids": active_ids,
        },
    )


@login_required
def frequency_step(request: HttpRequest, is_onboarding: bool = True) -> HttpResponse:
    profile = request.user.profile

    if request.method == "POST":
        form = FrequencyForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            if is_onboarding:
                profile.onboarding_completed_at = timezone.now()
                profile.save(update_fields=["onboarding_completed_at"])
                return redirect(reverse("feed:list"))
            messages.success(request, "Preferences updated.")
            return redirect("accounts:settings_notifications")
    else:
        form = FrequencyForm(instance=profile)

    return render(
        request,
        "accounts/frequency_form.html",
        {"form": form, "is_onboarding": is_onboarding, "step": 3},
    )


@login_required
def account(request: HttpRequest) -> HttpResponse:
    profile = request.user.profile
    journal_count = request.user.journal_subscriptions.filter(is_active=True).count()
    saved_count = request.user.paper_states.filter(saved_at__isnull=False).count()
    email_verified = EmailAddress.objects.filter(
        user=request.user, email=request.user.email, verified=True
    ).exists()
    return render(
        request,
        "accounts/account.html",
        {
            "profile": profile,
            "journal_count": journal_count,
            "saved_count": saved_count,
            "email_verified": email_verified,
            "initials": initials(profile.full_name, request.user.email),
            "active_tab": "account",
        },
    )


@login_required
def delete_account_confirm(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        confirm_text = (request.POST.get("confirm_text") or "").strip().upper()
        if confirm_text != "DELETE":
            messages.error(request, 'Type "DELETE" to confirm.')
            return render(request, "accounts/delete_account_confirm.html", {})

        user = request.user
        logout(request)
        user.delete()
        messages.success(request, "Your account has been deleted.")
        return redirect("landing")

    return render(request, "accounts/delete_account_confirm.html", {})
