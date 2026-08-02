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
from apps.feed.filters import FeedFilters
from apps.feed.models import FeaturedPaper, UserPaperState
from apps.papers.models import Paper, PaperSpecialty, PaperSummary
from engine.featured import week_start_for

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

    Setting up a specialty makes the fixture representative of a configured
    reader whose personalised feed has content to assert against.
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
    """Nine queries, none of them per-card and none of them per-week.

    session, user, profile (middleware), the feed page itself, the week-count
    aggregate behind the group headings, the UserPaperState lookup, the
    UserPaperState insert, and the feed_last_viewed_at update. The ceiling keeps
    headroom for one more so a library upgrade doesn't fail the build
    spuriously; the properties that actually matter are asserted below.

    The aggregate raised this from seven when week grouping landed. It is one
    query bounded to the weeks the page already spans, not one per heading —
    which is what the two invariance tests below exist to hold it to.
    """
    subscribe(user, circulation)
    for i in range(5):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20) - timedelta(days=i))

    count = _feed_query_count(client, user)
    assert count <= 9


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


def test_feed_view_query_count_does_not_scale_with_the_number_of_weeks(
    client, cardiology, circulation
):
    """The N+1 guard for grouping, on the axis grouping actually introduced.

    Same page size, same card count, same everything except how many week
    headings the page ends up drawing. The moment counts are fetched per heading
    instead of in one bounded aggregate, these diverge.
    """
    # Separate journals, so each reader sees only their own papers and the two
    # feeds differ in nothing but how many weeks the same 8 cards fall across.
    other = Journal.objects.create(
        slug="jacc", pubmed_name="JACC", display_name="JACC", short_name="JACC"
    )
    SpecialtyJournal.objects.create(specialty=cardiology, journal=other)

    one_week = make_user("oneweek@example.com", cardiology)
    eight_weeks = make_user("eightweeks@example.com", cardiology)
    subscribe(one_week, circulation)
    subscribe(eight_weeks, other)

    for i in range(8):
        make_paper(f"same-{i}", journal=circulation, feed_date=date(2026, 7, 20))
        make_paper(f"spread-{i}", journal=other, feed_date=date(2026, 7, 20) - timedelta(weeks=i))

    packed = _feed_query_count(client, one_week)
    spread = _feed_query_count(client, eight_weeks)

    assert packed == spread, (
        f"{packed} queries for 1 week but {spread} for 8 — a per-week query crept in"
    )


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


# ---------------------------------------------------------------- week grouping


def feature(paper: Paper, specialty: Specialty, *, week_start: date) -> FeaturedPaper:
    return FeaturedPaper.objects.create(
        paper=paper, specialty=specialty, week_start=week_start, rank=1, score=1.0
    )


def test_a_week_count_is_the_whole_week_not_just_the_page(user, circulation):
    """The entire reason the heading needs an aggregate rather than len().

    A heading that counted the page would read "(5)" above a week holding 25 and
    change every time the reader scrolled.
    """
    subscribe(user, circulation)
    for i in range(25):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 22))

    page = services.get_feed_page(user, page_size=5)

    assert len(page.weeks) == 1
    assert len(page.weeks[0].papers) == 5
    assert page.weeks[0].total == 25


def test_week_counts_survive_an_already_ordered_queryset(user, circulation):
    """The silent one: ORDER BY columns get folded into the GROUP BY.

    Handed a queryset already carrying the feed's
    (-feed_date, -is_priority_study, -id), the aggregate groups by the paper
    itself and reports 1 for every week — no error, and correct-looking in any
    fixture where each week holds a single paper. Three papers in one week with
    mixed priority flags is what makes the failure visible.

    get_feed_page passes the unordered queryset, so this asserts the guard
    inside week_counts rather than the path that happens to avoid it.
    """
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), is_priority_study=True)
    make_paper("2", journal=circulation, feed_date=date(2026, 7, 21))
    make_paper("3", journal=circulation, feed_date=date(2026, 7, 22), is_priority_study=True)

    ordered = services.filtered_queryset(user, FeedFilters()).order_by(*services.FEED_SORT.order_by)
    totals = services.week_counts(
        ordered, date_field="feed_date", oldest=date(2026, 7, 20), newest=date(2026, 7, 22)
    )

    assert totals == {week_start_for(date(2026, 7, 20)): 3}
    # ...and the same total reaches the heading through the real code path.
    assert services.get_feed_page(user).weeks[0].total == 3


def test_week_counts_are_not_split_by_the_featured_annotation(user, circulation, cardiology):
    """feed_queryset annotates is_featured; values() must mask it out of GROUP BY.

    If it ever leaks, each week splits into a featured and a non-featured row
    and every count silently halves.
    """
    subscribe(user, circulation)
    starred = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    make_paper("2", journal=circulation, feed_date=date(2026, 7, 21))
    feature(starred, cardiology, week_start=date(2026, 7, 27))

    page = services.get_feed_page(user)

    assert len(page.weeks) == 1
    assert page.weeks[0].total == 2


def test_the_python_week_label_and_the_sql_week_bucket_agree(user, circulation):
    """week_start_for() names the heading; date_trunc('week') fills the count.

    A one-week drift between them would file a correct total under the wrong
    heading, and it would first show up at a year boundary rather than in
    everyday data.
    """
    subscribe(user, circulation)
    days = [date(2026, 12, 28), date(2027, 1, 1), date(2027, 1, 4), date(2027, 1, 10)]
    for i, day in enumerate(days):
        make_paper(str(i), journal=circulation, feed_date=day)

    page = services.get_feed_page(user)

    assert {group.week_start for group in page.weeks} == {week_start_for(day) for day in days}
    # 28 Dec and 1 Jan are the same Monday; 4 Jan and 10 Jan are the next one.
    assert {group.total for group in page.weeks} == {2}


def test_a_week_count_respects_the_design_filter(user, circulation):
    """A heading reading (5) that opens on 2 trials is a bug report."""
    subscribe(user, circulation)
    for i in range(5):
        paper = make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20))
        if i >= 2:
            PaperSummary.objects.filter(paper=paper).update(study_type="Cohort")

    page = services.get_feed_page(user, filters=FeedFilters(design="rct"))

    assert len(page.weeks) == 1
    assert page.weeks[0].total == 2


def test_a_week_count_respects_the_unseen_tab(user, circulation):
    """Unseen thins a week as you read it, and the heading has to keep up."""
    subscribe(user, circulation)
    papers = [
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20)) for i in range(4)
    ]
    for paper in papers[:3]:
        UserPaperState.objects.create(user=user, paper=paper, opened_at="2026-07-21T00:00:00Z")

    page = services.get_feed_page(user, filters=FeedFilters(tab="unseen"))

    assert len(page.weeks) == 1
    assert page.weeks[0].total == 1


def test_a_week_with_nothing_in_it_gets_no_heading(user, circulation):
    """Empty weeks fall out of the GROUP BY rather than showing up as "(0)".

    A gap under a narrow filter should thin the list, not pad it with rows that
    open onto nothing.
    """
    subscribe(user, circulation)
    make_paper("recent", journal=circulation, feed_date=date(2026, 7, 22))
    # Nothing at all in the weeks between, then one much older paper.
    make_paper("old", journal=circulation, feed_date=date(2026, 6, 3))

    page = services.get_feed_page(user)

    assert [g.week_start for g in page.weeks] == [date(2026, 7, 20), date(2026, 6, 1)]
    assert all(g.total == 1 for g in page.weeks)


def test_a_tampered_cursor_does_not_swallow_the_first_heading(user, circulation):
    """previous_week and apply_cursor have to fall back together.

    They decode the same cursor, so a junk one must yield page-one content
    *with* its heading rather than page one silently missing its first heading.
    """
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 22))

    for junk in ("not-base64!!", "eyJub3QiOiAiYSBjdXJzb3IifQ==", "///"):
        page = services.get_feed_page(user, cursor=junk)
        assert [p.pmid for p in page.papers] == ["1"]
        assert page.weeks[0].show_header is True


def test_weeks_are_grouped_by_the_active_sort_field(user, circulation):
    """Grouping on feed_date under sort=pub would produce non-monotonic headings."""
    subscribe(user, circulation)
    make_paper(
        "1",
        journal=circulation,
        feed_date=date(2026, 7, 20),
        pub_date=date(2026, 6, 1),
        pub_sort_date=date(2026, 6, 1),
    )

    by_feed = services.get_feed_page(user, filters=FeedFilters(sort="feed"))
    by_pub = services.get_feed_page(user, filters=FeedFilters(sort="pub"))

    assert by_feed.weeks[0].week_start == week_start_for(date(2026, 7, 20))
    assert by_pub.weeks[0].week_start == week_start_for(date(2026, 6, 1))


def test_a_week_split_across_a_page_boundary_gets_one_heading(user, circulation):
    """Page 2 continues the week it interrupted rather than re-announcing it."""
    subscribe(user, circulation)
    for i in range(5):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 22))

    first = services.get_feed_page(user, page_size=3)
    second = services.get_feed_page(user, cursor=first.next_cursor, page_size=3)

    assert first.weeks[0].show_header is True
    assert second.weeks[0].week_start == first.weeks[0].week_start
    assert second.weeks[0].show_header is False


def test_a_page_that_opens_a_new_week_gets_a_heading(user, circulation):
    """The other half of the boundary rule — suppressing it always would be wrong."""
    subscribe(user, circulation)
    for i in range(3):
        make_paper(f"a{i}", journal=circulation, feed_date=date(2026, 7, 22))
    for i in range(3):
        make_paper(f"b{i}", journal=circulation, feed_date=date(2026, 7, 15))

    first = services.get_feed_page(user, page_size=3)
    second = services.get_feed_page(user, cursor=first.next_cursor, page_size=3)

    assert second.weeks[0].week_start == week_start_for(date(2026, 7, 15))
    assert second.weeks[0].show_header is True


def test_the_newest_two_weeks_of_the_first_page_open_expanded(user, circulation):
    """The fortnight you are actually reading, without a click.

    Anchored on the newest weeks that have papers rather than on the calendar:
    ingestion is nightly, so a Monday-morning reader whose current week is still
    empty must not be met by a wall of closed headings.
    """
    subscribe(user, circulation)
    for week in range(4):
        make_paper(
            str(week), journal=circulation, feed_date=date(2026, 7, 22) - timedelta(weeks=week)
        )

    page = services.get_feed_page(user)

    assert [group.is_open for group in page.weeks] == [True, True, False, False]


def test_later_pages_do_not_reopen_weeks(user, circulation):
    """is_open is a first-page decision; page 2 must not fight the reader."""
    subscribe(user, circulation)
    for i in range(6):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 22) - timedelta(weeks=i))

    first = services.get_feed_page(user, page_size=3)
    second = services.get_feed_page(user, cursor=first.next_cursor, page_size=3)

    assert not any(group.is_open for group in second.weeks)


def test_the_featured_tab_labels_weeks_by_the_papers_own_dates(user, circulation, cardiology):
    """A selection run is stamped a week ahead of the papers it selected.

    gather_candidates takes feed_date in [week_start - 7d, week_start), so a run
    stamped 3 Aug holds papers dated 27 Jul-2 Aug. Labelling headings from
    FeaturedPaper.week_start would print a date the cards underneath contradict.
    """
    subscribe(user, circulation)
    picked = make_paper("1", journal=circulation, feed_date=date(2026, 7, 29))
    feature(picked, cardiology, week_start=date(2026, 8, 3))

    page = services.get_feed_page(user, filters=FeedFilters(tab="featured"))

    assert [p.pmid for p in page.papers] == ["1"]
    assert page.weeks[0].week_start == date(2026, 7, 27)  # the paper's week
    assert page.weeks[0].week_start != date(2026, 8, 3)  # not the run's stamp


def test_the_featured_tab_groups_without_a_specialty_costing_a_query(user, circulation):
    """No specialty means no featured picks — and none() must not hit the DB."""
    user.profile.specialty = None
    user.profile.save()
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))

    with CaptureQueriesContext(connection) as ctx:
        page = services.get_feed_page(user, filters=FeedFilters(tab="featured"))

    assert page.papers == []
    assert page.weeks == []
    assert ctx.captured_queries == []


def test_the_feed_renders_a_heading_per_week_with_its_total(client, user, circulation):
    subscribe(user, circulation)
    for i in range(3):
        make_paper(f"a{i}", journal=circulation, feed_date=date(2026, 7, 22))
    make_paper("b", journal=circulation, feed_date=date(2026, 7, 15))
    client.force_login(user)

    resp = client.get(reverse("feed:list"))

    assert resp.content.count(b"week-header") == 2
    assert b'data-week-toggle="2026-07-20"' in resp.content
    assert b'data-week-toggle="2026-07-13"' in resp.content
    assert b'<span class="week-count">(3)</span>' in resp.content


def test_a_week_split_across_pages_renders_its_heading_once(client, user, circulation):
    """The rendered half of the boundary rule, end to end through the view."""
    subscribe(user, circulation)
    for i in range(5):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 22))
    client.force_login(user)

    with override_settings(FEED_PAGE_SIZE=3):
        first = client.get(reverse("feed:list"))
        cursor = services.get_feed_page(user, page_size=3).next_cursor
        second = client.get(reverse("feed:list"), {"cursor": cursor})

    assert first.content.count(b'data-week-toggle="2026-07-20"') == 1
    assert second.content.count(b'data-week-toggle="2026-07-20"') == 0


def test_cards_in_a_closed_week_render_hidden(client, user, circulation):
    """Collapsed is a server-rendered fact, so there is no open-then-snap-shut."""
    subscribe(user, circulation)
    for week in range(3):
        make_paper(
            str(week), journal=circulation, feed_date=date(2026, 7, 22) - timedelta(weeks=week)
        )
    client.force_login(user)

    resp = client.get(reverse("feed:list"))

    assert resp.content.count(b"<article") == 3
    # Newest two weeks open, the third closed.
    assert resp.content.count(b"hidden>") == 1
    assert resp.content.count(b'aria-expanded="true"') == 2
    assert resp.content.count(b'aria-expanded="false"') == 1


def test_earlier_weeks_load_on_click_once_the_tail_is_collapsed(client, user, circulation):
    """The runaway guard: hidden cards have no height for scrolling to act on."""
    subscribe(user, circulation)
    for week in range(4):
        make_paper(
            str(week), journal=circulation, feed_date=date(2026, 7, 22) - timedelta(weeks=week)
        )
    client.force_login(user)

    with override_settings(FEED_PAGE_SIZE=3):
        resp = client.get(reverse("feed:list"))

    assert b'hx-trigger="click"' in resp.content
    assert b"Show earlier weeks" in resp.content
    assert b'hx-trigger="revealed"' not in resp.content


def test_the_saved_list_renders_without_week_headings(client, user, circulation):
    """Saved is ordered by when you saved it, so feed-date weeks would misorder."""
    subscribe(user, circulation)
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    UserPaperState.objects.create(user=user, paper=paper, saved_at="2026-07-21T00:00:00Z")
    client.force_login(user)

    resp = client.get(reverse("feed:read_later"))

    assert b"Paper 1" in resp.content
    assert b"week-header" not in resp.content
    assert b"data-week=" not in resp.content


def test_a_later_empty_page_does_not_inject_an_empty_state(client, user, circulation):
    """The sentinel replaces itself, so a stray empty state would land mid-feed."""
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)

    stale = services.encode_cursor(make_paper("2", journal=circulation, feed_date=date(2020, 1, 1)))
    resp = client.get(reverse("feed:list"), {"cursor": stale})

    assert b"empty-state" not in resp.content


def test_an_empty_feed_produces_no_weeks_and_costs_no_aggregate(user, circulation):
    """No papers means nothing to count — and no query to count it with."""
    subscribe(user, circulation)

    with CaptureQueriesContext(connection) as ctx:
        page = services.get_feed_page(user)

    assert page.weeks == []
    assert not any("DATE_TRUNC" in q["sql"].upper() for q in ctx.captured_queries)


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


def test_search_finds_separated_partial_title_terms(client, user, circulation):
    subscribe(user, circulation)
    make_paper(
        "1",
        journal=circulation,
        feed_date=date(2026, 7, 20),
        title="Colchicine treatment after acute myocardial infarction",
    )
    make_paper(
        "2",
        journal=circulation,
        feed_date=date(2026, 7, 19),
        title="Colchicine in chronic coronary disease",
    )
    client.force_login(user)

    resp = client.get(reverse("feed:search"), {"q": "colch acute myo"})
    assert b"Colchicine treatment after acute myocardial infarction" in resp.content
    assert b"Colchicine in chronic coronary disease" not in resp.content


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


def test_selecting_a_search_result_adds_it_to_recent_searches(client, user, circulation):
    subscribe(user, circulation)
    paper = make_paper(
        "1", journal=circulation, feed_date=date(2026, 7, 20), title="Recent search paper"
    )
    client.force_login(user)

    results = client.get(reverse("feed:search"), {"q": "Recent"})
    assert f"{reverse('paper_detail', args=[paper.pmid])}?from=search".encode() in results.content

    client.get(reverse("paper_detail", args=[paper.pmid]), {"from": "search"})
    assert UserPaperState.objects.get(user=user, paper=paper).searched_at is not None

    history = client.get(reverse("feed:search"))
    assert b"Recent searches" in history.content
    assert b"Recent search paper" in history.content


def test_recent_searches_are_private_to_the_reader(client, user, circulation, cardiology):
    paper = make_paper(
        "1", journal=circulation, feed_date=date(2026, 7, 20), title="Private search paper"
    )
    other_user = make_user("other@example.com", cardiology)
    UserPaperState.objects.create(user=other_user, paper=paper, searched_at="2026-07-20T12:00:00Z")
    client.force_login(user)

    resp = client.get(reverse("feed:search"))
    assert b"Private search paper" not in resp.content


def test_recent_searches_are_newest_first(client, user, circulation):
    older = make_paper("1", journal=circulation, feed_date=date(2026, 7, 19), title="Older search")
    newer = make_paper("2", journal=circulation, feed_date=date(2026, 7, 20), title="Newer search")
    UserPaperState.objects.create(user=user, paper=older, searched_at="2026-07-20T10:00:00Z")
    UserPaperState.objects.create(user=user, paper=newer, searched_at="2026-07-20T11:00:00Z")
    client.force_login(user)

    body = client.get(reverse("feed:search")).content
    assert body.index(b"Newer search") < body.index(b"Older search")


# ---------------------------------------------------------------- public paper detail


def test_public_paper_detail_is_visible_without_login(client, circulation):
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), title="Public note")
    resp = client.get(reverse("paper_detail", args=[paper.pmid]))
    assert resp.status_code == 200
    assert b"Public note" in resp.content
    assert b"AI-generated from the PubMed abstract" in resp.content


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


def test_infinite_scroll_hangs_off_a_sentinel_not_a_card(client, user, circulation):
    """A card is not a stable anchor for the next page — it can be dismissed."""
    subscribe(user, circulation)
    for i in range(4):
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)

    with override_settings(FEED_PAGE_SIZE=2):
        resp = client.get(reverse("feed:list"))

    assert resp.content.count(b'class="feed-sentinel"') == 1
    assert b'hx-trigger="revealed"' in resp.content
    # The trigger used to ride the last <article>; no card should carry it now.
    cards = [chunk.split(b"</article>")[0] for chunk in resp.content.split(b"<article")[1:]]
    assert cards, "expected the feed to render cards"
    assert not any(b"hx-trigger" in card for card in cards)


def test_infinite_scroll_survives_dismissing_the_last_card(client, user, circulation):
    """Dismissing the card that used to carry the trigger ended the feed silently.

    The sentinel is a separate element, so removing any card leaves paging intact.
    """
    subscribe(user, circulation)
    papers = [
        make_paper(str(i), journal=circulation, feed_date=date(2026, 7, 20)) for i in range(4)
    ]
    client.force_login(user)

    with override_settings(FEED_PAGE_SIZE=2):
        resp = client.get(reverse("feed:list"))
        assert b'class="feed-sentinel"' in resp.content

        client.post(reverse("feed:dismiss", args=[papers[-1].pmid]))
        resp = client.get(reverse("feed:list"))

    assert b'class="feed-sentinel"' in resp.content


def test_the_last_page_of_the_feed_has_no_sentinel(client, user, circulation):
    """Otherwise the reader sits under a skeleton that never resolves."""
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20))
    client.force_login(user)

    resp = client.get(reverse("feed:list"))
    assert b"feed-sentinel" not in resp.content


def test_a_trial_shows_one_design_badge_not_two(client, user, circulation):
    """PubMed's is_rct flag and the summariser's study_type both say "RCT"."""
    subscribe(user, circulation)
    make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), is_rct=True)
    client.force_login(user)

    resp = client.get(reverse("feed:list"))
    assert resp.content.count(b'<span class="badge">RCT</span>') == 1


def test_a_paper_that_is_not_a_trial_shows_the_summarisers_design(client, user, circulation):
    subscribe(user, circulation)
    paper = make_paper("1", journal=circulation, feed_date=date(2026, 7, 20), is_rct=False)
    PaperSummary.objects.filter(paper=paper).update(study_type="Meta-analysis")
    client.force_login(user)

    resp = client.get(reverse("feed:list"))
    assert b'<span class="badge">Meta-analysis</span>' in resp.content
    assert b'<span class="badge">RCT</span>' not in resp.content


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
