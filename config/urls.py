from django.contrib import admin
from django.urls import include, path

from apps.common.views import healthz, landing
from apps.feed.views import paper_detail

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", landing, name="landing"),
    path("", include("apps.accounts.urls")),
    path("feed/", include("apps.feed.urls")),
    path("p/<str:pmid>/", paper_detail, name="paper_detail"),
    path("healthz", healthz, name="healthz"),
]
