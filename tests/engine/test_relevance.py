"""Specialty relevance matching — the general-journal filter.

The cardiology vocabulary here is the real one from
cardiology-feed/specialties/cardiology.json.
"""

import pytest

from engine.pubmed.parse import parse_efetch_response
from engine.relevance import SpecialtyRules, match_specialty

CARDIOLOGY = SpecialtyRules(
    slug="cardiology",
    mesh_terms=(
        "Cardiovascular Diseases",
        "Heart Diseases",
        "Cardiology",
        "Myocardial Infarction",
        "Heart Failure",
        "Arrhythmias, Cardiac",
        "Coronary Artery Disease",
        "Atrial Fibrillation",
        "Hypertension",
    ),
    title_keywords=(
        "cardiac",
        "cardiovascular",
        "heart",
        "arrhythmia",
        "myocardial",
        "coronary",
        "atrial fibrillation",
        "heart failure",
        "cardiomyopathy",
        "valvular",
    ),
    abstract_keywords=("heart failure", "myocardial infarction", "atrial fibrillation"),
)


def test_matches_on_mesh_descriptor():
    result = match_specialty(
        rules=CARDIOLOGY,
        mesh_terms=["Humans", "Heart Failure", "Aged"],
        title="An unrelated-sounding title",
    )
    assert result.matched
    assert result.matched_mesh == ("Heart Failure",)
    assert result.matched_keywords == ()


def test_matches_on_title_keyword_with_no_mesh_at_all():
    """The MeSH-lag case: a brand-new paper still has to be findable."""
    result = match_specialty(
        rules=CARDIOLOGY,
        mesh_terms=[],
        title="Sacubitril in acute heart failure: a pragmatic trial",
    )
    assert result.matched
    assert result.matched_mesh == ()
    assert "heart" in result.matched_keywords


def test_matches_on_abstract_when_title_is_opaque():
    result = match_specialty(
        rules=CARDIOLOGY,
        mesh_terms=[],
        title="The PARADIGM-HF investigators report",
        abstract="Patients with reduced ejection fraction and heart failure were enrolled.",
    )
    assert result.matched
    assert result.matched_keywords == ("heart failure",)


def test_does_not_match_an_unrelated_paper():
    result = match_specialty(
        rules=CARDIOLOGY,
        mesh_terms=["Melanoma", "Nivolumab", "Humans"],
        title="Ten-year outcomes in advanced melanoma",
        abstract="Overall survival after immunotherapy in advanced melanoma.",
    )
    assert not result.matched
    assert result.matched_mesh == ()
    assert result.matched_keywords == ()


def test_mesh_matching_is_case_insensitive_but_exact():
    """MeSH is a controlled vocabulary: exact descriptors, not substrings."""
    assert match_specialty(rules=CARDIOLOGY, mesh_terms=["heart failure"]).matched
    # "Heart Failure, Diastolic" is a different descriptor and must not match
    # the "Heart Failure" rule by substring.
    result = match_specialty(rules=CARDIOLOGY, mesh_terms=["Heart Failure, Diastolic"])
    assert result.matched_mesh == ()


@pytest.mark.parametrize(
    ("title", "should_match"),
    [
        ("Cardiac output during exercise", True),
        ("The cardiac cycle", True),
        # Whole-word matching: these must NOT match on "cardiac"/"heart"/"mi".
        ("Surface roughness of implants", False),
        ("Family medicine workforce trends", False),
        ("Hearty meals and nutrition outcomes", False),
        ("Discardiac terminology in records", False),
    ],
)
def test_keywords_match_whole_words_not_substrings(title, should_match):
    """A substring test would let 'cardiac' match 'discardiac' and 'heart'
    match 'hearty' — in a clinical vocabulary that is not hypothetical."""
    assert match_specialty(rules=CARDIOLOGY, title=title).matched is should_match


def test_no_rules_never_matches():
    empty = SpecialtyRules(slug="empty")
    assert not match_specialty(rules=empty, mesh_terms=["Heart Failure"], title="cardiac").matched


# ---------------------------------------------------------------- stem wildcards

STEMMED = SpecialtyRules(
    slug="cardiology",
    title_keywords=("cardi*", "myocard*", "arrhythm*", "atrial fibrillation"),
)


@pytest.mark.parametrize(
    ("title", "should_match"),
    [
        # All of these are real or realistic titles that plain whole-word
        # matching on "cardiac" misses.
        ("Cardiotoxic Effects after Gene Therapy for DMD", True),
        ("Cardiomyopathy in young athletes", True),
        ("Cardiovascular outcomes at ten years", True),
        ("Pericarditis after vaccination", False),  # stem is anchored at a word start
        ("Myocarditis surveillance", True),
        ("Arrhythmogenic right ventricular dysplasia", True),
        ("Antiarrhythmic drug selection", False),  # again, must start the word
        # The stem must not swallow unrelated words.
        ("Cardinal symptoms of sepsis", True),  # accepted cost of a "cardi*" stem
        ("Discarded specimens in the laboratory", False),
    ],
)
def test_stem_wildcards(title, should_match):
    assert match_specialty(rules=STEMMED, title=title).matched is should_match


def test_stem_and_exact_terms_coexist():
    result = match_specialty(rules=STEMMED, title="Atrial fibrillation and cardiomyopathy")
    assert result.matched
    assert set(result.matched_keywords) == {"cardi*", "atrial fibrillation"}


def test_a_bare_asterisk_never_matches():
    rules = SpecialtyRules(slug="x", title_keywords=("*",))
    assert not match_specialty(rules=rules, title="anything at all").matched


# ---------------------------------------------------------------- against real records


def test_general_journal_cardiology_paper_matches(pubmed_xml):
    """A real NEJM cardiomyopathy trial must reach a cardiologist's feed."""
    article = parse_efetch_response(pubmed_xml("general_with_mesh"))[0]
    result = match_specialty(
        rules=CARDIOLOGY,
        mesh_terms=article.mesh_terms,
        title=article.title,
        abstract=article.abstract,
    )
    assert result.matched


def test_general_journal_melanoma_paper_does_not_match(pubmed_xml):
    """A real NEJM melanoma trial must not."""
    article = parse_efetch_response(pubmed_xml("unrelated_general"))[0]
    result = match_specialty(
        rules=CARDIOLOGY,
        mesh_terms=article.mesh_terms,
        title=article.title,
        abstract=article.abstract,
    )
    assert not result.matched, f"unexpected match: {result}"
