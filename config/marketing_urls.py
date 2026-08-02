"""Public pages served only on MedPally's marketing hostnames."""

from django.urls import path

from apps.common import views

handler404 = views.marketing_not_found
handler500 = views.marketing_server_error

urlpatterns = [
    path("", views.marketing_home, name="marketing_home"),
    path("privacy/", views.marketing_privacy, name="marketing_privacy"),
    path("terms/", views.marketing_terms, name="marketing_terms"),
    path("ai-summaries/", views.marketing_ai_summaries, name="marketing_ai_summaries"),
    path("robots.txt", views.marketing_robots, name="marketing_robots"),
    path("sitemap.xml", views.marketing_sitemap, name="marketing_sitemap"),
    path("favicon.ico", views.favicon),
]
