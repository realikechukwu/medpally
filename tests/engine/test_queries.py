from datetime import date

import pytest

from engine.pubmed.queries import (
    build_date_clause,
    build_journal_query,
    build_search_term,
    chunked,
    normalize_journal_name,
)


def test_build_journal_query():
    assert build_journal_query(["Circulation"]) == '("Circulation"[jour])'
    assert build_journal_query(["Circulation", "Heart"]) == '("Circulation"[jour] OR "Heart"[jour])'


def test_build_journal_query_rejects_an_empty_list():
    with pytest.raises(ValueError, match="at least one journal"):
        build_journal_query([])


@pytest.mark.parametrize(
    "name",
    [
        # Real names from cardiology-feed/specialties/*.json with characters
        # that a hand-built query string could mangle.
        "Journal of Primary Care & Community Health",
        "African Journal of Primary Health Care & Family Medicine",
        "Primary Health Care Research & Development",
        "JACC: Cardiovascular Imaging",
        "Heart (British Cardiac Society)",
        "Journal of Neurosurgery: Spine",
    ],
)
def test_journal_names_with_punctuation_survive_intact(name):
    """These go into a POST body, which requests form-encodes, so no escaping
    happens here. The test pins that we are not mangling them on the way in."""
    assert f'"{name}"[jour]' in build_journal_query([name])


def test_build_date_clause_uses_entrez_date():
    clause = build_date_clause(date(2026, 7, 1), date(2026, 7, 27))
    assert clause == '("2026/07/01"[edat] : "2026/07/27"[edat])'
    assert "[dp]" not in clause


def test_build_date_clause_rejects_a_reversed_window():
    with pytest.raises(ValueError, match="is after"):
        build_date_clause(date(2026, 7, 27), date(2026, 7, 1))


def test_build_search_term_combines_both_parts():
    term = build_search_term(["Circulation"], date(2026, 7, 20), date(2026, 7, 27))
    assert term == '("Circulation"[jour]) AND ("2026/07/20"[edat] : "2026/07/27"[edat])'


def test_chunked_splits_evenly_and_keeps_the_remainder():
    assert chunked(["a", "b", "c", "d", "e"], 2) == [["a", "b"], ["c", "d"], ["e"]]
    assert chunked([], 2) == []
    assert len(chunked([str(i) for i in range(100)], 40)) == 3


def test_chunked_rejects_a_zero_size():
    with pytest.raises(ValueError, match=">= 1"):
        chunked(["a"], 0)


@pytest.mark.parametrize(
    ("variants", "count"),
    [
        # Each group is the SAME journal spelled differently across
        # cardiology-feed's config files and PubMed's own responses. They must
        # all normalise to one key.
        (["Lancet", "The Lancet", "Lancet (London, England)".replace(" (London, England)", "")], 1),
        (["BMJ", "The BMJ", "the bmj"], 1),
        (["JACC: Cardiovascular Imaging", "JACC Cardiovascular Imaging"], 1),
        (["Heart", "Heart "], 1),
        (
            [
                "The New England journal of medicine",
                "New England Journal of Medicine",
                "the new england journal of medicine",
            ],
            1,
        ),
        (["European Heart Journal", "European heart journal"], 1),
        # Genuinely different journals must not collapse.
        (["Heart", "Heart Rhythm"], 2),
        (["Circulation", "Circulation: Heart Failure"], 2),
    ],
)
def test_normalize_journal_name_collapses_variants(variants, count):
    assert len({normalize_journal_name(v) for v in variants}) == count
