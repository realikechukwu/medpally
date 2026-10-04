"""Turning a shared link, DOI, PMID or page title into one PubMed record."""

from __future__ import annotations

from datetime import date

import pytest

from engine.pubmed.lookup import (
    Reference,
    clean_title,
    find_article,
    parse_reference,
    title_search_term,
)
from engine.pubmed.models import FetchedArticle, JournalIdentity


def article(pmid: str, title: str) -> FetchedArticle:
    return FetchedArticle(
        pmid=pmid,
        title=title,
        abstract="x" * 300,
        journal=JournalIdentity(title="The New England journal of medicine"),
        pub_date_raw="2020 Dec 31",
        pub_date=date(2020, 12, 31),
        entrez_date=date(2020, 9, 1),
    )


class FakeSource:
    """Records every PubMed call, answering from canned tables."""

    def __init__(self, searches=None, records=None):
        self.searches: dict[str, list[str]] = searches or {}
        self.records: dict[str, FetchedArticle] = records or {}
        self.calls: list[tuple[str, object]] = []

    def search_pmids(self, term, *, retmax=5, sort=""):
        self.calls.append(("search", term))
        return self.searches.get(term, [])[:retmax]

    def efetch_by_pmid(self, pmids):
        self.calls.append(("fetch", tuple(pmids)))
        return [self.records[p] for p in pmids if p in self.records]


@pytest.mark.parametrize(
    ("text", "title", "expected"),
    [
        ("https://pubmed.ncbi.nlm.nih.gov/32865380/?utm_source=x", "", Reference(pmid="32865380")),
        ("https://www.ncbi.nlm.nih.gov/pubmed/32865380", "", Reference(pmid="32865380")),
        ("See PMID: 32865380 for the trial.", "", Reference(pmid="32865380")),
        ("  32865380 ", "", Reference(pmid="32865380")),
        ("https://pmc.ncbi.nlm.nih.gov/articles/PMC7727327/", "", Reference(pmcid="PMC7727327")),
        (
            "https://www.nejm.org/doi/full/10.1056/NEJMoa2021372?query=featured_home",
            "",
            Reference(doi="10.1056/NEJMoa2021372"),
        ),
        (
            "https://www.ahajournals.org/doi/10.1161%2FCIRCULATIONAHA.123.065432",
            "",
            Reference(doi="10.1161/CIRCULATIONAHA.123.065432"),
        ),
        (
            "Read https://doi.org/10.1016/S0140-6736(23)01234-5).",
            "",
            Reference(doi="10.1016/S0140-6736(23)01234-5"),
        ),
        (
            "https://onlinelibrary.wiley.com/doi/epdf/10.1002/ejhf.2915",
            "",
            Reference(doi="10.1002/ejhf.2915"),
        ),
        (
            "https://www.nature.com/articles/s41586-026-11044-y",
            "",
            Reference(doi="10.1038/s41586-026-11044-y"),
        ),
        (
            "https://www.nature.com/articles/s41591-025-03456-7.pdf?utm_source=share",
            "",
            Reference(doi="10.1038/s41591-025-03456-7"),
        ),
        ("nature.com/articles/nm.2345#Sec2", "", Reference(doi="10.1038/nm.2345")),
        (
            "https://www.thelancet.com/journals/lancet/article/PIIS0140-6736(23)01234-5/fulltext",
            "",
            Reference(pii="S0140-6736(23)01234-5"),
        ),
        (
            "https://www.sciencedirect.com/science/article/pii/S0735109723063805",
            "",
            Reference(pii="S0735-1097(23)06380-5"),
        ),
        (
            "https://jamanetwork.com/journals/jama/fullarticle/2812345",
            "Effect of Semaglutide on Heart Failure Outcomes | Cardiology | JAMA | JAMA Network",
            Reference(title="Effect of Semaglutide on Heart Failure Outcomes"),
        ),
        (
            "Colchicine in Patients with Chronic Coronary Disease - PubMed",
            "",
            Reference(title="Colchicine in Patients with Chronic Coronary Disease"),
        ),
        ("https://example.com/", "Home", Reference()),
    ],
)
def test_parse_reference_finds_the_strongest_identifiers(text, title, expected):
    assert parse_reference(text, title) == expected


def test_an_identifier_in_the_link_wins_over_loose_text():
    ref = parse_reference("Worth reading https://pubmed.ncbi.nlm.nih.gov/123456/ tonight")
    assert ref == Reference(pmid="123456")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Colchicine in Patients with Chronic Coronary Disease | NEJM",
            "Colchicine in Patients with Chronic Coronary Disease",
        ),
        (
            "“Lipoprotein(a) and incident coronary events in women”",
            "Lipoprotein(a) and incident coronary events in women",
        ),
        ("NEJM", ""),
        ("", ""),
    ],
)
def test_clean_title_keeps_only_the_article_title(raw, expected):
    assert clean_title(raw) == expected


def test_title_search_needs_three_meaningful_words():
    assert title_search_term("Colchicine in Patients with Chronic Coronary Disease") == (
        "colchicine[ti] AND patients[ti] AND chronic[ti] AND coronary[ti] AND disease[ti]"
    )
    assert title_search_term("The use of it") == ""


def test_a_pmid_is_fetched_without_searching():
    source = FakeSource(records={"32865380": article("32865380", "Colchicine trial")})

    found = find_article(Reference(pmid="32865380"), source)

    assert found is not None and found.pmid == "32865380"
    assert source.calls == [("fetch", ("32865380",))]


def test_a_doi_is_searched_exactly():
    term = '"10.1056/NEJMoa2021372"[doi]'
    source = FakeSource(
        searches={term: ["32865380"]},
        records={"32865380": article("32865380", "Colchicine trial")},
    )

    found = find_article(Reference(doi="10.1056/NEJMoa2021372"), source)

    assert found is not None and found.pmid == "32865380"
    assert source.calls[0] == ("search", term)


def test_a_doi_pubmed_does_not_know_falls_back_to_the_title():
    title = "Colchicine in Patients with Chronic Coronary Disease"
    source = FakeSource(
        searches={title_search_term(title): ["111", "32865380"]},
        records={
            "111": article("111", "Colchicine for gout flares in primary care"),
            "32865380": article(
                "32865380", "Colchicine in Patients with Chronic Coronary Disease."
            ),
        },
    )

    found = find_article(Reference(doi="10.9999/not-indexed", title=title), source)

    assert found is not None and found.pmid == "32865380"


def test_a_title_that_only_resembles_a_record_is_not_accepted():
    title = "Colchicine in Patients with Chronic Coronary Disease"
    source = FakeSource(
        searches={title_search_term(title): ["111"]},
        records={"111": article("111", "Colchicine in patients with acute pericarditis")},
    )

    assert find_article(Reference(title=title), source) is None


def test_nothing_to_look_up_finds_nothing():
    source = FakeSource()
    assert find_article(Reference(), source) is None
    assert source.calls == []
