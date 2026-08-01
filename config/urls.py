from django.contrib import admin
from django.urls import include, path

from apps.common.views import favicon, healthz, landing, manifest, offline, service_worker
from apps.feed.views import paper_detail

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", landing, name="landing"),
    path("", include("apps.accounts.urls")),
    path("feed/", include("apps.feed.urls")),
    path("p/<str:pmid>/", paper_detail, name="paper_detail"),
    path("healthz", healthz, name="healthz"),
    # A service worker only controls pages at or below its own path, so this
    # one has to be served from the root rather than from /static/.
    path("sw.js", service_worker, name="service_worker"),
    path("manifest.webmanifest", manifest, name="manifest"),
    path("offline/", offline, name="offline"),
    path("favicon.ico", favicon),
]
