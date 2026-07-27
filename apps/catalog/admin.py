from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db.models import Count, QuerySet
from django.http import HttpRequest
from django.utils.html import format_html

from .models import Journal, JournalAlias, Specialty, SpecialtyJournal


class SpecialtyJournalInline(admin.TabularInline):
    model = SpecialtyJournal
    extra = 0
    autocomplete_fields = ("journal",)


class JournalAliasInline(admin.TabularInline):
    model = JournalAlias
    extra = 0
    fields = ("value", "kind", "value_normalized")
    readonly_fields = ("value_normalized",)


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "journal_count", "vocabulary_size", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (SpecialtyJournalInline,)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Specialty]:
        return super().get_queryset(request).annotate(_journals=Count("journal_links"))

    @admin.display(description="journals", ordering="_journals")
    def journal_count(self, obj: Specialty) -> int:
        return obj._journals  # type: ignore[attr-defined]

    @admin.display(description="vocabulary")
    def vocabulary_size(self, obj: Specialty) -> str:
        return f"{len(obj.mesh_terms)} MeSH / {len(obj.title_keywords)} title / {len(obj.abstract_keywords)} abstract"


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = (
        "cover",
        "display_name",
        "short_name",
        "pubmed_name",
        "medline_ta",
        "nlm_uid",
        "is_general",
        "is_active",
        "resolved",
    )
    list_filter = ("is_general", "is_active", "cover_style", "specialties")
    search_fields = ("display_name", "pubmed_name", "short_name", "medline_ta", "nlm_uid", "slug")
    prepopulated_fields = {"slug": ("display_name",)}
    inlines = (JournalAliasInline,)
    actions = ("mark_inactive", "mark_active")

    fieldsets = (
        (None, {"fields": ("slug", "display_name", "short_name", "is_general", "is_active")}),
        (
            "PubMed",
            {
                "fields": ("pubmed_name", "medline_ta", "nlm_uid", "issn_print", "issn_electronic"),
                "description": (
                    'pubmed_name is what goes inside "…"[jour]. If it returns no articles the '
                    "journal is silently absent from every feed — run "
                    "<code>manage.py resolve_journals --slug &lt;slug&gt; --force</code> to check. "
                    "MEDLINE abbreviations are more reliable than full titles."
                ),
            },
        ),
        ("Cover", {"fields": ("cover_color", "cover_color_accent", "cover_style")}),
    )

    @admin.display(description="")
    def cover(self, obj: Journal) -> Any:
        return format_html(
            '<span style="display:inline-block;min-width:64px;padding:4px 8px;border-radius:4px;'
            'background:{};color:#fff;font:600 11px system-ui;text-align:center">{}</span>',
            obj.cover_color,
            obj.short_name,
        )

    @admin.display(description="resolved", boolean=True)
    def resolved(self, obj: Journal) -> bool:
        return obj.is_resolved

    @admin.action(description="Mark selected journals inactive")
    def mark_inactive(self, request: HttpRequest, queryset: QuerySet[Journal]) -> None:
        self.message_user(request, f"{queryset.update(is_active=False)} journals deactivated")

    @admin.action(description="Mark selected journals active")
    def mark_active(self, request: HttpRequest, queryset: QuerySet[Journal]) -> None:
        self.message_user(request, f"{queryset.update(is_active=True)} journals activated")


@admin.register(JournalAlias)
class JournalAliasAdmin(admin.ModelAdmin):
    list_display = ("value", "kind", "journal", "value_normalized")
    list_filter = ("kind",)
    search_fields = ("value", "value_normalized", "journal__display_name")
    autocomplete_fields = ("journal",)
    readonly_fields = ("value_normalized",)
