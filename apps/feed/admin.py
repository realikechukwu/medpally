from __future__ import annotations

from django.contrib import admin

from .models import UserPaperState


@admin.register(UserPaperState)
class UserPaperStateAdmin(admin.ModelAdmin):
    list_display = ("user", "paper", "opened_at", "saved_at", "liked_at", "dismissed_at")
    list_filter = ("saved_at", "liked_at", "dismissed_at")
    search_fields = ("user__email", "paper__pmid", "paper__title")
    autocomplete_fields = ("user", "paper")
