"""The feed query, keyset pagination, seen-state, and the HTMX views."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import UserJournalSubscription
from apps.catalog.models import Journal, Specialty, SpecialtyJournal
from apps.feed import services
from apps.feed.models import UserPaperState
from apps.papers.models import Paper, PaperSpecialty, PaperSummary

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def cardiology():
    return Specialty.objects.create(slug="cardiology", name="Cardiology")


@pytest.fixture
def circulation(cardiology):
    j = Journal.objects.create(
        slug="circulation", pubmed_name="Circulation", display_name="Circulation", short_name="Circ"
    )
    SpecialtyJournal.objects.create(specialty=cardiology, journal=j)
    return j


@pytest.fixture
def nejm(cardiology):
    j = Journal.objects.create(
        slug="nejm", pubmed_name="NEJM", display_name="NEJM", short_name="NEJM", is_general=True
    )
    return j


def make_user(email: str, specialty: Specialty):
    """An onboarded user.

    onboarding_completed_at is not optional here: without it OnboardingMiddleware
    302s every feed request to the wizard and content assertions run against an
    empty body.
    """
    u = User.objects.create_user(email=email, password="pw12345!")
    u.profile.specialty = specialty
    u.profile.onboarding_completed_at = "2026-01-01T00:00:00Z"
    u.profile.save()
    return u


@pytest.fixture
def user(cardiology):
    return make_user("reader@example.com", cardiology)


def make_paper(pmid: str, *, journal: Journal, feed_date: date, **kwargs) -> Paper:
    defaults = {
        "title": f"Paper {pmid}",
        "abstract": "x" * 250,
        "journal": journal,
        "journal_name_raw": journal.display_name,
        "entrez_date": feed_date,
        "feed_date": feed_date,
        "category": Paper.Category.STANDARD,
        "summary_status": Paper.SummaryStatus.OK,
        "is_visible": True,
    }
    defaults.update(kwargs)
    paper = Paper.objects.create(pmid=pmid, **defaults)
    PaperSummary.objects.create(
        paper=paper,
        study_type="RCT",
        context="context",
        finding="finding",
        so_what="so what",
        tags=["Tag"],
        model_name="fake",
        prompt_version="v1",
    )
    return paper


def subscribe(user, *journals: Journal) -> None:
    for j in journals:
        UserJournalSubscription.objects.get_or_create(user=user, journal=j)


# ---------------------------------------------------------------- feed_queryset filtering


def test_specialty_journal_papers_included_regardless_of_topic(user, circulation, cardiology):
    subscribe(user, circulation)
    paper = make_paper(
        "1", journal=circulation, feed_date=date(2026, 7, 20), title="Something unrelated"
    )
    assert paper in list(services.feed_queryset(user))


def test_general_journal_paper_excluded_without_specialty_match(user, nejm, cardiology):
    subscribe(user, nejm)
    make_paper("1", journal=nejm, feed_date=date(2026, 7, 20))  # no PaperSpecialty link
    assert list(services.feed_queryset(user)) == []


def test_general_journal_paper_included_with_specialty_match(user, nejm, cardiology):
    subscribe(user, nejm)
    paper = make_paper("1", journal=nejm, feed_date=date(2026, 7, 20))
    PaperSpecialty.objects.create(paper=paper, specialty=cardiology, relevance="topical_match")
    assert paper in list(services.feed_queryset(user))


def test_unsubscribed_journal_is_excluded(user, circulation):
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    assert list(services.feed_queryset(user)) == []


def test_invisible_or_unsummarised_papers_are_excluded(user, circulation):
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), is_visible=False)
    make_paper(
        "2",
        journal=circulation,
        feed_date=date(2026, 7, 20),
        summary_status=Paper.SummaryStatus.PENDING,
    )
    assert list(services.feed_queryset(user)) == []


def test_dismissed_papers_are_hard_hidden(user, circulation):
    subscribe(user, circulation)
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    UserPaperState.objects.create(user=user, paper=paper, dismissed_at="2026-07-20T00:00:00Z")
    assert list(services.feed_queryset(user)) == []


def test_ordering_is_recency_then_priority(user, circulation):
    subscribe(user, circulation)
    older = make_paper("1", journal=circulation, feed_date=date(2026, 7, 18))
    newer_standard = make_paper("2", journal=circulation, feed_date=date(2026, 7, 20))
    newer_priority = make_paper(
        "3", journal=circulation, feed_date=date(2026, 7, 20), is_priority_study=True
    )
    ordered = list(services.feed_queryset(user))
    assert ordered == [newer_priority, newer_standard, older]


# ---------------------------------------------------------------- keyset pagination


def test_keyset_pagination_over_tied_dates_has_no_duplicates_or_gaps(user, circulation):
    subscribe(user, circulation)
    same_day = date(2026, 7, 20)
    papers = [make_paper(str(i), journal=circulation, feed_date=same_day) for i in range(55)]
    expected_ids = {p.id for p in papers}

    seen_ids: set[int] = set()
    cursor = None
    for _ in range(10):  # more than enough pages at page_size=20 for 55 papers
        page = services.get_feed_page(user, cursor=cursor, page_size=20)
        seen_ids.update(p.id for p in page.papers)
        if not page.next_cursor:
            break
        cursor = page.next_cursor

    assert seen_ids == expected_ids


def test_unseen_toggle_excludes_papers_the_reader_opened(user, circulation):
    subscribe(user, circulation)
    seen = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    make_paper("2", journal=circulation, feed_date=date(2026, 7, 19))
    UserPaperState.objects.create(user=user, paper=seen, opened_at="2026-07-20T00:00:00Z")

    page = services.get_feed_page(user, unseen_only=True)
    assert [p.pmid for p in page.papers] == ["2"]


# ---------------------------------------------------------------- seen state


def test_unseen_feed_load_is_a_pure_read(client, user, circulation):
    subscribe(user, circulation)
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)

    first = client.get(reverse("feed:list"), {"tab": "unseen"})
    second = client.get(reverse("feed:list"), {"tab": "unseen"})

    assert paper.title.encode() in first.content
    assert paper.title.encode() in second.content
    assert not UserPaperState.objects.filter(user=user, paper=paper).exists()


# ---------------------------------------------------------------- feed view


def _feed_query_count(client, user) -> int:
    client.force_login(user)
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("feed:list"))
    assert response.status_code == 200
    return len(ctx.captured_queries)


def test_feed_view_query_count_is_bounded(client, user, circulation):
    """Eight queries, none of them per-card.

    session, user, profile (middleware), the feed page itself, the
    UserPaperState lookup, the UserPaperState insert, and the
    feed_last_viewed_at update. The ceiling has headroom for one more so a
    library upgrade doesn't fail the build spuriously; the property that
    actually matters is asserted in the test below.
    """
    subscribe(user, circulation)
    for i in range(5):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20) - timedelta(days=i))

    count = _feed_query_count(client, user)
    assert count <= 8


def test_feed_view_query_count_does_not_scale_with_page_size(client, cardiology, circulation):
    """The N+1 guard. A fixed ceiling drifts; invariance to page size does not.

    This is the assertion worth more than fifty unit tests: the moment a
    template tweak reaches through a card into paper.journal or paper.summary
    without select_related, the 30-card count diverges from the 3-card count.
    """
    small = make_user("small@example.com", cardiology)
    large = make_user("large@example.com", cardiology)
    subscribe(small, circulation)
    subscribe(large, circulation)

    for i in range(30):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20) - timedelta(days=i))

    with override_settings(FEED_PAGE_SIZE=3):
        few = _feed_query_count(client, small)
    with override_settings(FEED_PAGE_SIZE=30):
        many = _feed_query_count(client, large)

    assert few == many, f"{few} queries for 3 cards but {many} for 30 — an N+1 crept in"


def test_feed_view_renders_cards(client, user, circulation):
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), title="A notable finding")
    client.force_login(user)
    response = client.get(reverse("feed:list"))
    assert b"A notable finding" in response.content


def test_signed_in_feed_includes_progressive_tab_navigation(client, user, circulation):
    """The normal hrefs remain, but JavaScript can swap and cache these tabs."""
    subscribe(user, circulation)
    client.force_login(user)

    response = client.get(reverse("feed:list"))

    assert b'data-navigation-user="' in response.content
    assert b'id="app-main"' in response.content
    assert b'id="bottom-nav"' in response.content
    assert response.content.count(b"data-tab-nav") >= 8  # bottom navigation and drawer


def test_two_specialties_see_genuinely_different_feeds(client, cardiology, circulation):
    gp = Specialty.objects.create(slug="gp", name="GP")
    gp_journal = Journal.objects.create(
        slug="bmj", pubmed_name="BMJ", display_name="BMJ", short_name="BMJ"
    )
    SpecialtyJournal.objects.create(specialty=gp, journal=gp_journal)

    cardiologist = make_user("cardio@example.com", cardiology)
    subscribe(cardiologist, circulation)

    gp_doctor = make_user("gp@example.com", gp)
    subscribe(gp_doctor, gp_journal)

    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), title="Cardiology paper")
    make_paper("2", journal=gp_journal, feed_date=date(2026, 7, 20), title="GP paper")

    client.force_login(cardiologist)
    resp = client.get(reverse("feed:list"))
    assert b"Cardiology paper" in resp.content
    assert b"GP paper" not in resp.content

    client.force_login(gp_doctor)
    resp = client.get(reverse("feed:list"))
    assert b"GP paper" in resp.content
    assert b"Cardiology paper" not in resp.content


# ---------------------------------------------------------------- save/like/dismiss


def test_toggle_save_moves_a_card_to_read_later(client, user, circulation):
    subscribe(user, circulation)
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)

    resp = client.get(reverse("feed:read_later"))
    assert paper.pmid.encode() not in resp.content or b"Nothing saved yet" in resp.content

    client.post(reverse("feed:toggle_save", args=[paper.pmid]))
    state = UserPaperState.objects.get(user=user, paper=paper)
    assert state.saved_at is not None

    resp = client.get(reverse("feed:read_later"))
    assert b"Paper 1" in resp.content


def test_toggle_save_is_a_toggle(client, user, circulation):
    subscribe(user, circulation)
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)

    client.post(reverse("feed:toggle_save", args=[paper.pmid]))
    assert UserPaperState.objects.get(paper=paper).saved_at is not None

    client.post(reverse("feed:toggle_save", args=[paper.pmid]))
    assert UserPaperState.objects.get(paper=paper).saved_at is None


def test_toggle_like(client, user, circulation):
    subscribe(user, circulation)
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)
    client.post(reverse("feed:toggle_like", args=[paper.pmid]))
    assert UserPaperState.objects.get(paper=paper).liked_at is not None


def test_dismiss_removes_paper_from_feed(client, user, circulation):
    subscribe(user, circulation)
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)

    client.post(reverse("feed:dismiss", args=[paper.pmid]))
    assert UserPaperState.objects.get(paper=paper).dismissed_at is not None

    resp = client.get(reverse("feed:list"))
    assert b"Paper 1" not in resp.content


# ---------------------------------------------------------------- search


def test_search_finds_a_paper_by_title(client, user, circulation):
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), title="Colchicine after MI")
    make_paper("2", journal=circulation, feed_date=date(2026, 7, 19), title="Unrelated study")
    client.force_login(user)

    resp = client.get(reverse("feed:search"), {"q": "colchicine"})
    assert resp.status_code == 200
    assert b"Colchicine after MI" in resp.content
    assert b"Unrelated study" not in resp.content


def test_search_finds_a_paper_by_pmid(client, user, circulation):
    subscribe(user, circulation)
    make_paper("42508842", journal=circulation, feed_date=date(2026, 7, 20), title="Findable by ID")
    client.force_login(user)

    resp = client.get(reverse("feed:search"), {"q": "42508842"})
    assert b"Findable by ID" in resp.content


def test_search_is_scoped_to_the_readers_subscriptions(client, user, circulation, nejm, cardiology):
    """A paper in a journal the reader never subscribed to must not leak into search."""
    subscribe(user, circulation)
    make_paper("1", journal=nejm, feed_date=date(2026, 7, 20), title="Unsubscribed journal paper")
    client.force_login(user)

    resp = client.get(reverse("feed:search"), {"q": "Unsubscribed"})
    assert b"Unsubscribed journal paper" not in resp.content


def test_search_with_no_query_shows_no_results(client, user, circulation):
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), title="Should not appear")
    client.force_login(user)

    resp = client.get(reverse("feed:search"))
    assert resp.status_code == 200
    assert b"Should not appear" not in resp.content


# ---------------------------------------------------------------- public paper detail


def test_public_paper_detail_is_visible_without_login(client, circulation):
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), title="Public note")
    resp = client.get(reverse("paper_detail", args=[paper.pmid]))
    assert resp.status_code == 200
    assert b"Public note" in resp.content


def test_public_paper_detail_404s_for_unsummarised_paper(client, circulation):
    paper = make_paper(
        "1",
        journal=circulation,
        feed_date=date(2026, 7, 20),
        summary_status=Paper.SummaryStatus.PENDING,
    )
    PaperSummary.objects.filter(paper=paper).delete()
    resp = client.get(reverse("paper_detail", args=[paper.pmid]))
    assert resp.status_code == 404


def test_public_paper_detail_does_not_render_the_raw_abstract(client, circulation):
    paper = make_paper(
        "1",
        journal=circulation,
        feed_date=date(2026, 7, 20),
        abstract="THIS RAW ABSTRACT TEXT MUST NEVER APPEAR " * 5,
    )
    resp = client.get(reverse("paper_detail", args=[paper.pmid]))
    assert b"THIS RAW ABSTRACT TEXT MUST NEVER APPEAR" not in resp.content


# ---------------------------------------------------------------- hardening


def test_malformed_cursor_falls_back_to_the_first_page(client, user, circulation):
    """A cursor is a query param, so it arrives tampered with and truncated."""
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), title="Still here")
    client.force_login(user)

    for junk in ("not-base64!!", "", "eyJub3QiOiAiYSBjdXJzb3IifQ==", "///"):
        resp = client.get(reverse("feed:list"), {"cursor": junk})
        assert resp.status_code == 200
        assert b"Still here" in resp.content


def test_decode_cursor_returns_none_rather_than_raising():
    assert services.decode_cursor("not-base64!!") is None
    assert services.decode_cursor("eyJub3QiOiAiYSBjdXJzb3IifQ==") is None


def test_share_page_is_reachable_midway_through_onboarding(client, cardiology, circulation):
    """The share link is the growth loop; onboarding must not swallow it."""
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), title="Shared note")
    half_done = User.objects.create_user(email="half@example.com", password="pw12345!")
    assert half_done.profile.onboarding_completed_at is None
    client.force_login(half_done)

    resp = client.get(reverse("paper_detail", args=[paper.pmid]))
    assert resp.status_code == 200
    assert b"Shared note" in resp.content


def test_opening_the_share_page_records_opened_at(client, user, circulation):
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)
    client.get(reverse("paper_detail", args=[paper.pmid]))
    assert UserPaperState.objects.get(user=user, paper=paper).opened_at is not None


def test_invisible_paper_cannot_be_saved(client, user, circulation):
    """Posting the endpoint directly must not reach a paper pulled from the feed."""
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), is_visible=False)
    client.force_login(user)

    resp = client.post(reverse("feed:toggle_save", args=[paper.pmid]))
    assert resp.status_code == 404
    assert not UserPaperState.objects.filter(user=user, paper=paper).exists()


def test_new_divider_renders_once_not_per_card(client, user, circulation):
    subscribe(user, circulation)
    for i in range(3):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20))
    user.profile.feed_last_viewed_at = "2020-01-01T00:00:00Z"  # everything is new
    user.profile.save()
    client.force_login(user)

    resp = client.get(reverse("feed:list"))
    assert resp.content.count(b"New since your last visit") == 1


def test_read_later_paginates(client, user, circulation):
    subscribe(user, circulation)
    papers = [
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20)) for i in range(7)
    ]
    client.force_login(user)
    for paper in papers:
        client.post(reverse("feed:toggle_save", args=[paper.pmid]))

    seen: set[int] = set()
    cursor = None
    for _ in range(10):
        page = services.get_saved_page(user, cursor=cursor, page_size=3)
        seen.update(s.paper_id for s in page.states)
        if not page.next_cursor:
            break
        cursor = page.next_cursor

    assert seen == {p.id for p in papers}
