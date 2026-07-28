from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.papers.models import Paper

from . import services
from .models import UserPaperState


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


@login_required
def feed_list(request: HttpRequest) -> HttpResponse:
    cursor = request.GET.get("cursor") or None
    unseen_only = request.GET.get("unseen") == "1"

    page = services.get_feed_page(request.user, cursor=cursor, unseen_only=unseen_only)
    previously_seen_states = services.attach_state_and_record_impressions(request.user, page.papers)

    profile = request.user.profile
    last_viewed_at = profile.feed_last_viewed_at
    if cursor is None:
        profile.feed_last_viewed_at = timezone.now()
        profile.save(update_fields=["feed_last_viewed_at"])

    cards = [
        {
            "paper": paper,
            "state": previously_seen_states.get(paper.id),
            "is_new": bool(last_viewed_at and paper.ingested_at > last_viewed_at),
        }
        for paper in page.papers
    ]

    context = {"cards": cards, "next_cursor": page.next_cursor, "unseen_only": unseen_only}
    template = "feed/_cards.html" if _is_htmx(request) else "feed/list.html"
    return render(request, template, context)


@login_required
def read_later(request: HttpRequest) -> HttpResponse:
    states = (
        UserPaperState.objects.filter(user=request.user, saved_at__isnull=False)
        .select_related("paper", "paper__journal", "paper__summary")
        .order_by("-saved_at")
    )
    cards = [{"paper": s.paper, "state": s, "is_new": False} for s in states]
    return render(request, "feed/read_later.html", {"cards": cards})


def paper_detail(request: HttpRequest, pmid: str) -> HttpResponse:
    """Public share page: the generated note and a PubMed link, no login."""
    paper = get_object_or_404(
        Paper.objects.select_related("journal", "summary"),
        pmid=pmid,
        is_visible=True,
        summary_status=Paper.SummaryStatus.OK,
    )
    return render(request, "feed/paper_detail.html", {"paper": paper})


def _get_or_create_state(user, paper: Paper) -> UserPaperState:
    state, _ = UserPaperState.objects.get_or_create(user=user, paper=paper)
    return state


@login_required
@require_POST
def toggle_save(request: HttpRequest, pmid: str) -> HttpResponse:
    paper = get_object_or_404(Paper, pmid=pmid)
    state = _get_or_create_state(request.user, paper)
    state.saved_at = None if state.saved_at else timezone.now()
    state.save(update_fields=["saved_at", "updated_at"])
    return render(request, "feed/_save_button.html", {"paper": paper, "state": state})


@login_required
@require_POST
def toggle_like(request: HttpRequest, pmid: str) -> HttpResponse:
    paper = get_object_or_404(Paper, pmid=pmid)
    state = _get_or_create_state(request.user, paper)
    state.liked_at = None if state.liked_at else timezone.now()
    state.save(update_fields=["liked_at", "updated_at"])
    return render(request, "feed/_like_button.html", {"paper": paper, "state": state})


@login_required
@require_POST
def dismiss(request: HttpRequest, pmid: str) -> HttpResponse:
    paper = get_object_or_404(Paper, pmid=pmid)
    state = _get_or_create_state(request.user, paper)
    state.dismissed_at = timezone.now()
    state.save(update_fields=["dismissed_at", "updated_at"])
    return HttpResponse("")
