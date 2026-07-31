from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.papers.models import Paper

from . import services
from .filters import FeedFilters
from .models import UserPaperState


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


@login_required
def feed_list(request: HttpRequest) -> HttpResponse:
    filters = FeedFilters.from_request(request)

    page = services.get_feed_page(request.user, filters=filters)
    states = services.attach_state(request.user, page.papers)

    profile = request.user.profile
    last_viewed_at = profile.feed_last_viewed_at
    if not filters.cursor and filters.is_default():
        profile.feed_last_viewed_at = timezone.now()
        profile.save(update_fields=["feed_last_viewed_at"])

    cards = [
        {
            "paper": paper,
            "state": states.get(paper.id),
            "is_new": bool(last_viewed_at and paper.ingested_at > last_viewed_at),
        }
        for paper in page.papers
    ]

    # The divider is a heading for the run of new cards, so it belongs above
    # the first of them exactly once — not stamped on every new card, and not
    # repeated on page 2 when infinite scroll appends more.
    if not filters.cursor:
        for card in cards:
            if card["is_new"]:
                card["show_new_divider"] = True
                break

    context = {
        "cards": cards,
        "next_cursor": page.next_cursor,
        "filters": filters,
        "next_url_name": "feed:list",
        "active_tab": "feed",
        "empty_title": "Pick a specialty to see this week's top papers"
        if filters.tab == "featured" and not profile.specialty_id
        else "No papers yet",
        "empty_body": "Choose a specialty in your profile to see featured papers."
        if filters.tab == "featured" and not profile.specialty_id
        else "Check back after tonight's ingestion run, or widen your journal picks in Account > Journals.",
    }
    template = "feed/_cards.html" if _is_htmx(request) else "feed/list.html"
    return render(request, template, context)


@login_required
def read_later(request: HttpRequest) -> HttpResponse:
    page = services.get_saved_page(request.user, cursor=request.GET.get("cursor") or None)
    cards = [{"paper": s.paper, "state": s, "is_new": False} for s in page.states]
    context = {
        "cards": cards,
        "next_cursor": page.next_cursor,
        "next_url_name": "feed:read_later",
        "active_tab": "saved",
    }
    template = "feed/_cards.html" if _is_htmx(request) else "feed/read_later.html"
    return render(request, template, context)


@login_required
def search(request: HttpRequest) -> HttpResponse:
    """Search across the papers this reader is entitled to see.

    Scoped to feed_queryset (subscriptions + specialty + visibility) rather
    than all papers, matching the product's "search your journals" framing —
    not a global PubMed search.
    """
    query = (request.GET.get("q") or "").strip()
    cards: list[dict] = []
    if query:
        matches = (
            services.feed_queryset(request.user)
            .filter(
                Q(title__icontains=query)
                | Q(pmid=query)
                | Q(journal__display_name__icontains=query)
            )
            .order_by("-feed_date", "-id")[:40]
        )
        cards = [{"paper": p, "state": None} for p in matches]
    return render(
        request, "feed/search.html", {"query": query, "cards": cards, "active_tab": "search"}
    )


def paper_detail(request: HttpRequest, pmid: str) -> HttpResponse:
    """Public share page: the generated note and a PubMed link, no login."""
    paper = get_object_or_404(
        Paper.objects.select_related("journal", "summary"),
        pmid=pmid,
        is_visible=True,
        summary_status=Paper.SummaryStatus.OK,
    )
    state = None
    if request.user.is_authenticated:
        state, _ = UserPaperState.objects.update_or_create(
            user=request.user, paper=paper, defaults={"opened_at": timezone.now()}
        )
    return render(request, "feed/paper_detail.html", {"paper": paper, "state": state})


def _get_actionable_paper(pmid: str) -> Paper:
    """Only a paper the user could actually have seen can be acted on.

    Without the visibility filter any pmid in the table can be saved or liked
    by posting to the endpoint directly, including papers pulled from the feed
    by an admin.
    """
    return get_object_or_404(
        Paper, pmid=pmid, is_visible=True, summary_status=Paper.SummaryStatus.OK
    )


def _get_or_create_state(user, paper: Paper) -> UserPaperState:
    state, _ = UserPaperState.objects.get_or_create(user=user, paper=paper)
    return state


@login_required
@require_POST
def toggle_save(request: HttpRequest, pmid: str) -> HttpResponse:
    paper = _get_actionable_paper(pmid)
    state = _get_or_create_state(request.user, paper)
    state.saved_at = None if state.saved_at else timezone.now()
    state.save(update_fields=["saved_at", "updated_at"])
    return render(request, "feed/_save_button.html", {"paper": paper, "state": state})


@login_required
@require_POST
def toggle_like(request: HttpRequest, pmid: str) -> HttpResponse:
    paper = _get_actionable_paper(pmid)
    state = _get_or_create_state(request.user, paper)
    state.liked_at = None if state.liked_at else timezone.now()
    state.save(update_fields=["liked_at", "updated_at"])
    return render(request, "feed/_like_button.html", {"paper": paper, "state": state})


@login_required
@require_POST
def dismiss(request: HttpRequest, pmid: str) -> HttpResponse:
    paper = _get_actionable_paper(pmid)
    state = _get_or_create_state(request.user, paper)
    state.dismissed_at = timezone.now()
    state.save(update_fields=["dismissed_at", "updated_at"])
    return HttpResponse("")
