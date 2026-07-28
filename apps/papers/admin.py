"""Admin for papers, including the "map unresolved journal" workflow.

A paper with journal=None is invisible in every feed forever, and the only way
it happens is a [jour] term PubMed doesn't recognise (see resolve_journals).
The intermediate page here lets an admin pick the correct Journal once; that
choice is recorded as a JournalAlias so every paper sharing the same raw name —
past and future — resolves the same way.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.html import format_html

from apps.catalog.models import Journal, JournalAlias
from apps.ingestion.services import link_specialties_for_papers

from .models import Paper, PaperSpecialty, PaperSummary


class MapJournalForm(forms.Form):
    journal = forms.ModelChoiceField(queryset=Journal.objects.order_by("display_name"))


class PaperSummaryInline(admin.StackedInline):
    model = PaperSummary
    extra = 0
    can_delete = False


class PaperSpecialtyInline(admin.TabularInline):
    model = PaperSpecialty
    extra = 0
    readonly_fields = ("specialty", "relevance", "matched_mesh", "matched_keywords", "linked_at")
    can_delete = False


@admin.register(Paper)
class PaperAdmin(admin.ModelAdmin):
    list_display = (
        "pmid",
        "title_short",
        "journal_or_raw",
        "feed_date",
        "category",
        "summary_status",
        "is_visible",
    )
    list_filter = ("summary_status", "category", "is_visible", "is_priority_study", "is_rct")
    search_fields = ("pmid", "doi", "title", "journal_name_raw")
    autocomplete_fields = ("journal",)
    readonly_fields = ("ingested_at", "updated_at")
    inlines = (PaperSummaryInline, PaperSpecialtyInline)
    actions = ("map_unresolved_journal",)
    date_hierarchy = "feed_date"

    @admin.display(description="title")
    def title_short(self, obj: Paper) -> str:
        return obj.title[:80]

    @admin.display(description="journal")
    def journal_or_raw(self, obj: Paper) -> str:
        if obj.journal:
            return obj.journal.display_name
        return format_html(
            '<span style="color:#b91c1c">{} (unresolved)</span>', obj.journal_name_raw
        )

    @admin.action(description="Map unresolved journal…")
    def map_unresolved_journal(
        self, request: HttpRequest, queryset: QuerySet[Paper]
    ) -> HttpResponse:
        unresolved = queryset.filter(journal__isnull=True)
        if not unresolved.exists():
            self.message_user(request, "No unresolved papers in the selection.", messages.WARNING)
            return None

        raw_names = sorted(unresolved.values_list("journal_name_raw", flat=True).distinct())
        if len(raw_names) > 1:
            self.message_user(
                request,
                "Selected papers span more than one raw journal name; map them one name at a time.",
                messages.ERROR,
            )
            return None

        raw_name = raw_names[0]

        if "apply" in request.POST:
            form = MapJournalForm(request.POST)
            if form.is_valid():
                journal = form.cleaned_data["journal"]
                JournalAlias.objects.get_or_create(
                    value_normalized=JournalAlias.normalize(raw_name),
                    defaults={
                        "journal": journal,
                        "kind": JournalAlias.Kind.MANUAL,
                        "value": raw_name,
                    },
                )
                pmids = list(
                    Paper.objects.filter(
                        journal__isnull=True, journal_name_raw=raw_name
                    ).values_list("pmid", flat=True)
                )
                Paper.objects.filter(pmid__in=pmids).update(journal=journal)
                links_created = link_specialties_for_papers(pmids)
                self.message_user(
                    request,
                    f"Mapped {raw_name!r} to {journal} — {len(pmids)} papers updated, "
                    f"{links_created} specialty links created.",
                )
                return None
        else:
            form = MapJournalForm()

        return render(
            request,
            "admin/papers/map_journal.html",
            {
                "form": form,
                "raw_name": raw_name,
                "count": unresolved.count(),
                "opts": self.model._meta,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "queryset": queryset,
                "title": "Map unresolved journal",
            },
        )


@admin.register(PaperSummary)
class PaperSummaryAdmin(admin.ModelAdmin):
    list_display = ("paper", "study_type", "model_name", "prompt_version", "generated_at")
    list_filter = ("prompt_version", "model_name")
    search_fields = ("paper__pmid", "paper__title", "finding")
    autocomplete_fields = ("paper",)


@admin.register(PaperSpecialty)
class PaperSpecialtyAdmin(admin.ModelAdmin):
    list_display = ("paper", "specialty", "relevance", "linked_at")
    list_filter = ("specialty", "relevance")
    search_fields = ("paper__pmid", "paper__title")
    autocomplete_fields = ("paper", "specialty")
