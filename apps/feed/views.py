from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.papers.models import Paper

from . import services
from .filters import FeedFilters
from .models import UserPaperState


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _empty_state(filters: FeedFilters, profile) -> dict[str, str]:
    """Copy for a feed tab that came back with nothing.

    An empty tab is the only screen some readers see on day one, so it has to
    say what this tab is for and what to do next — in the reader's terms, not
    the pipeline's. Each tab fills up for a different reason and on a different
    schedule, so each gets its own wording rather than one shared apology.
    """
    if filters.tab == "featured":
        if not profile.specialty_id:
            return {
                "empty_title": "Tell us your specialty",
                "empty_body": "Featured picks are chosen one specialty at a time. "
                "Add yours and this tab fills up.",
                "empty_action_url": reverse("accounts:settings_profile"),
                "empty_action_label": "Choose your specialty",
            }
        return {
            "empty_title": "No featured picks yet",
            "empty_body": f"Every Monday we pick the strongest new {profile.specialty.name} "
            "studies. More journals on your list means more to choose from.",
            "empty_action_url": reverse("accounts:settings_journals"),
            "empty_action_label": "Add journals",
        }
    if filters.tab == "unseen":
        return {
            "empty_title": "You're all caught up",
            "empty_body": "You've opened every paper in your feed. New ones land each day.",
        }
    if filters.design:
        return {
            "empty_title": "Nothing matches this filter",
            "empty_body": "No papers of this study design in your feed right now. "
            "Switch back to Any design to see everything.",
        }
    return {
        "empty_title": "Your feed is empty",
        "empty_body": "New papers land each day. Adding more journals fills it out faster.",
        "empty_action_url": reverse("accounts:settings_journals"),
        "empty_action_label": "Add journals",
    }


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

    cards = {
        paper.id: {
            "paper": paper,
            "state": states.get(paper.id),
            "is_new": bool(last_viewed_at and paper.ingested_at > last_viewed_at),
        }
        for paper in page.papers
    }

    # The divider is a heading for the run of new cards, so it belongs above
    # the first of them exactly once — not stamped on every new card, and not
    # repeated on page 2 when infinite scroll appends more. It walks the flat
    # page order rather than the groups, because "first new card" means first
    # on the page, whichever week it landed in.
    if not filters.cursor:
        for paper in page.papers:
            if cards[paper.id]["is_new"]:
                cards[paper.id]["show_new_divider"] = True
                break

    groups = [
        {
            "week_start": week.week_start,
            "total": week.total,
            "show_header": week.show_header,
            "is_open": week.is_open,
            "cards": [cards[paper.id] for paper in week.papers],
        }
        for week in page.weeks
    ]

    context = {
        "groups": groups,
        "next_cursor": page.next_cursor,
        "filters": filters,
        "next_url_name": "feed:list",
        "active_tab": "feed",
        # Twenty hidden cards are barely a hundred pixels tall, so a "revealed"
        # sentinel under a collapsed week fires the moment it lands and chains
        # the whole archive in one burst. Once anything on the page is closed,
        # loading becomes something the reader asks for.
        "more_trigger": "revealed" if all(g["is_open"] for g in groups) else "click",
        "is_first_page": not filters.cursor,
        **_empty_state(filters, profile),
    }
    template = "feed/_cards.html" if _is_htmx(request) else "feed/list.html"
    return render(request, template, context)


@login_required
def read_later(request: HttpRequest) -> HttpResponse:
    cursor = request.GET.get("cursor") or None
    page = services.get_saved_page(request.user, cursor=cursor)
    cards = [{"paper": s.paper, "state": s, "is_new": False} for s in page.states]
    # Saved is ordered by when you saved a paper, not when it published, so week
    # headings keyed on feed_date would run out of order here. One open,
    # unlabelled group keeps the template to a single code path until Saved
    # grows big enough to want grouping on its own axis.
    groups = [{"week_start": None, "show_header": False, "is_open": True, "cards": cards}]
    context = {
        "groups": groups if cards else [],
        "next_cursor": page.next_cursor,
        "next_url_name": "feed:read_later",
        "active_tab": "saved",
        "more_trigger": "revealed",
        "is_first_page": not cursor,
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
