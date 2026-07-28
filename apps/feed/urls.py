from __future__ import annotations

from django.urls import path

from . import views

app_name = "feed"

urlpatterns = [
    path("", views.feed_list, name="list"),
    path("read-later/", views.read_later, name="read_later"),
    path("<str:pmid>/save/", views.toggle_save, name="toggle_save"),
    path("<str:pmid>/like/", views.toggle_like, name="toggle_like"),
    path("<str:pmid>/dismiss/", views.dismiss, name="dismiss"),
]
