from __future__ import annotations

from django.contrib import admin

from .models import IngestionRun, JournalFetchLog


class JournalFetchLogInline(admin.TabularInline):
    model = JournalFetchLog
    extra = 0
    readonly_fields = ("journal", "articles_found")
    can_delete = False
    autocomplete_fields = ("journal",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "command",
        "status",
        "date_from",
        "date_to",
        "articles_fetched",
        "papers_created",
        "papers_updated",
        "journals_unresolved",
        "summaries_ok",
        "duration_seconds",
    )
    list_filter = ("status", "command")
    readonly_fields = (
        "started_at",
        "finished_at",
        "duration_seconds",
        "date_from",
        "date_to",
        "journals_queried",
        "articles_fetched",
        "papers_created",
        "papers_updated",
        "specialty_links_created",
        "journals_unresolved",
        "summaries_attempted",
        "summaries_ok",
        "summaries_failed",
        "input_tokens",
        "output_tokens",
        "error",
    )
    inlines = (JournalFetchLogInline,)

    def has_add_permission(self, request) -> bool:
        return False
