"""The three onboarding forms, reused verbatim on the settings pages.

Journal selection is deliberately not a ModelForm over UserJournalSubscription
— a user picks Journals, not subscription rows, and the diff-on-save logic
(apps/accounts/services.py) is what turns that choice into rows.
"""

from __future__ import annotations

from django import forms

from apps.catalog.models import Journal, Specialty

from .models import Profile


class ProfileForm(forms.ModelForm):
    specialty = forms.ModelChoiceField(
        queryset=Specialty.objects.filter(is_active=True),
        widget=forms.RadioSelect,
        empty_label=None,
    )

    class Meta:
        model = Profile
        fields = ("full_name", "workplace", "specialty")
        widgets = {
            "full_name": forms.TextInput(attrs={"autofocus": True}),
        }


class JournalsForm(forms.Form):
    journals = forms.ModelMultipleChoiceField(
        queryset=Journal.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        error_messages={"required": "Pick at least one journal."},
    )


class FrequencyForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ("update_frequency",)
        widgets = {"update_frequency": forms.RadioSelect}
