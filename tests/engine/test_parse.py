"""Characterisation tests for the PubMed parser.

Every fixture except medline_date.xml is a real efetch response captured from
PubMed. These tests pin the parser's behaviour so the extraction from
cardiology-feed is provably behaviour-preserving.
"""

from datetime import date

import pytest

from engine.classify import EXCLUDED, PRIORITY
from engine.errors import ParseError
from engine.pubmed.parse import month_to_number, parse_efetch_response


def one(pubmed_xml, name):
    articles = parse_efetch_response(pubmed_xml(name))
    assert len(articles) == 1, f"{name} should hold exactly one article"
    return articles[0]


# ---------------------------------------------------------------- whole records


def test_rct_fields(pubmed_xml):
    a = one(pubmed_xml, "rct")
    assert a.pmid == "41662456"
    assert a.title.startswith("Incidence, Risk Factors, and Outcomes in Stressor-Associated")
    assert a.doi == "10.1161/CIRCULATIONAHA.125.076421"
    assert a.journal.title == "Circulation"
    assert a.journal.medline_ta == "Circulation"
    assert a.journal.nlm_unique_id == "0147763"
    assert a.journal.issn_electronic == "1524-4539"
    assert "Randomized Controlled Trial" in a.publication_types
    assert a.category == PRIORITY
    assert a.authors == ("Haimovich J", "Kany S", "Chang Y")
    assert "Atrial Fibrillation" in a.mesh_terms
    assert a.abstract.startswith("BACKGROUND:")
    assert len(a.abstract) > 2000


def test_editorial_is_excluded_and_has_no_abstract(pubmed_xml):
    a = one(pubmed_xml, "editorial")
    assert a.publication_types == ("Editorial", "Comment")
    assert a.category == EXCLUDED
    assert a.abstract == ""
    assert a.has_abstract is False
    assert a.mesh_terms == ()


def test_comment_without_abstract_is_excluded(pubmed_xml):
    a = one(pubmed_xml, "no_abstract")
    assert "Comment" in a.publication_types
    assert a.category == EXCLUDED
    assert a.abstract == ""


def test_study_protocol_is_excluded_by_publication_type(pubmed_xml):
    a = one(pubmed_xml, "study_protocol")
    assert "Clinical Trial Protocol" in a.publication_types
    assert a.category == EXCLUDED
    # It has a long abstract and MeSH terms — only the publication type saves us.
    assert len(a.abstract) > 2000


def test_structured_abstract_keeps_its_section_labels(pubmed_xml):
    a = one(pubmed_xml, "structured_abstract")
    assert a.abstract.startswith("IMPORTANCE:")
    for label in (
        "OBJECTIVES:",
        "DESIGN, SETTING, AND PARTICIPANTS:",
        "MAIN OUTCOMES AND MEASURES:",
        "RESULTS:",
        "CONCLUSIONS AND RELEVANCE:",
        "TRIAL REGISTRATION:",
    ):
        assert label in a.abstract, f"missing structured label {label}"
    # One line per section, not run together.
    assert a.abstract.count("\n") == 7


def test_url_is_derived_from_pmid(pubmed_xml):
    a = one(pubmed_xml, "rct")
    assert a.url == "https://pubmed.ncbi.nlm.nih.gov/41662456/"


# ---------------------------------------------------------------- dates


def test_entrez_date_can_lag_publication_date_by_months(pubmed_xml):
    """The reason the ingest window uses [edat] and not [dp].

    This real record was published 2025-11-08 but PubMed did not index it until
    2026-02-09 — three months later. A weekly job windowing on publication date
    looks for it in the week of 8 November, when it does not yet exist in
    PubMed, and never looks again. It would be missed entirely.
    """
    a = one(pubmed_xml, "rct")
    assert a.pub_date == date(2025, 11, 8)
    assert a.entrez_date == date(2026, 2, 9)
    assert (a.entrez_date - a.pub_date).days > 80


def test_entrez_date_can_precede_publication_date(pubmed_xml):
    """The same bug in the other direction: a forward-dated issue.

    Indexed 2025-08-20, stamped with an October issue date. Under [dp]
    windowing this surfaces two months after it was actually available.
    """
    a = one(pubmed_xml, "meta_analysis")
    assert a.entrez_date == date(2025, 8, 20)
    assert a.pub_date == date(2025, 10, 14)
    assert a.entrez_date < a.pub_date


def test_medline_date_yields_raw_string_and_no_parsed_date(pubmed_xml):
    a = one(pubmed_xml, "medline_date")
    assert a.pub_date_raw == "2023 Nov-Dec"
    assert a.pub_date is None  # a range is not a date; we do not invent one
    assert a.entrez_date == date(2023, 11, 14)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", 1),
        ("01", 1),
        ("12", 12),
        ("Jan", 1),
        ("jan", 1),
        ("January", 1),
        ("Dec", 12),
        ("Sep", 9),
        ("Sept", 9),
        ("", 0),
        ("Xyz", 0),
        ("13", 0),
        ("0", 0),
    ],
)
def test_month_to_number(raw, expected):
    assert month_to_number(raw) == expected


# ---------------------------------------------------------------- journals


@pytest.mark.parametrize(
    ("fixture", "title", "medline_ta", "nlm_uid"),
    [
        ("rct", "Circulation", "Circulation", "0147763"),
        ("meta_analysis", "European heart journal", "Eur Heart J", "8006263"),
        ("no_abstract", "Lancet (London, England)", "Lancet", "2985213R"),
        ("structured_abstract", "JAMA cardiology", "JAMA Cardiol", "101676033"),
        ("study_protocol", "BMJ open", "BMJ Open", "101552874"),
        ("general_with_mesh", "The New England journal of medicine", "N Engl J Med", "0255562"),
    ],
)
def test_journal_titles_vary_but_nlm_ids_do_not(pubmed_xml, fixture, title, medline_ta, nlm_uid):
    """Evidence for why Journal rows are keyed on NLM ID / ISSN, not on title.

    PubMed returns "European heart journal" (lowercase), "JAMA cardiology",
    "BMJ open" and "Lancet (London, England)" — none of which match the strings
    in cardiology-feed's specialties/*.json. Every one of them carries a stable
    NlmUniqueID.
    """
    a = one(pubmed_xml, fixture)
    assert a.journal.title == title
    assert a.journal.medline_ta == medline_ta
    assert a.journal.nlm_unique_id == nlm_uid


def test_journal_best_name_falls_back_through_the_identifiers(pubmed_xml):
    a = one(pubmed_xml, "rct")
    assert a.journal.best_name == "Circulation"


# ---------------------------------------------------------------- mesh


def test_mesh_descriptors_only_no_qualifiers(pubmed_xml):
    """Qualifiers are subheadings, not topics, and must not reach mesh_terms."""
    a = one(pubmed_xml, "meta_analysis")
    assert "Percutaneous Coronary Intervention" in a.mesh_terms
    for qualifier in ("methods", "mortality", "physiology"):
        assert qualifier not in a.mesh_terms


def test_recent_general_journal_paper_has_no_mesh_yet(pubmed_xml):
    """MeSH indexing lag, in real data.

    A July 2026 NEJM review with a full abstract and zero MeSH headings. Any
    relevance rule that reads only MeSH would never see this paper.
    """
    a = one(pubmed_xml, "general_no_mesh")
    assert a.mesh_terms == ()
    assert len(a.abstract) > 400


def test_mesh_terms_are_deduplicated(pubmed_xml):
    a = one(pubmed_xml, "unrelated_general")
    assert len(a.mesh_terms) == len(set(a.mesh_terms))


# ---------------------------------------------------------------- errors


def test_malformed_xml_raises_parse_error():
    with pytest.raises(ParseError):
        parse_efetch_response(b"<PubmedArticleSet><PubmedArticle>truncated")


def test_empty_result_set_is_not_an_error():
    assert parse_efetch_response(b"<PubmedArticleSet/>") == []


def test_records_without_a_pmid_are_dropped():
    xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation>
    <Article><ArticleTitle>No identifier</ArticleTitle></Article>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    assert parse_efetch_response(xml) == []
