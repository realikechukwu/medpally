"""Truth table for classification.

This is where every future regression in "what reaches a user's feed" will show
up first, so it is written as an exhaustive parametrised table rather than a few
happy-path assertions.
"""

import pytest

from engine.classify import (
    EXCLUDED,
    LOW_PRIORITY,
    PRIORITY,
    PRIORITY_NO_ABSTRACT,
    STANDARD,
    classify_article,
    is_priority_study,
    is_rct,
    normalize_study_type,
)


@pytest.mark.parametrize(
    ("pub_types", "has_abstract", "title", "expected"),
    [
        # Excluded publication types win over everything else, including a
        # priority type on the same record.
        (["Editorial"], True, "Rethinking LDL", EXCLUDED),
        (["Comment"], True, "A comment", EXCLUDED),
        (["Letter"], True, "A letter", EXCLUDED),
        (["News"], True, "Some news", EXCLUDED),
        (["Published Erratum"], True, "Erratum", EXCLUDED),
        (["Retraction of Publication"], True, "Retraction", EXCLUDED),
        (["Retracted Publication"], True, "Retracted", EXCLUDED),
        (["Study Protocol"], True, "A trial", EXCLUDED),
        (["Clinical Trial Protocol"], True, "A trial", EXCLUDED),
        (["Randomized Controlled Trial", "Editorial"], True, "Mixed", EXCLUDED),
        # Protocol detection by title.
        ([], True, "SOMETHING: study protocol for a trial", EXCLUDED),
        ([], True, "Trial protocol for a new device", EXCLUDED),
        ([], True, "A protocol for managing heart failure", EXCLUDED),
        ([], True, "New device: protocol", EXCLUDED),
        ([], True, "Research protocol overview", EXCLUDED),
        # "protocol" anywhere in the title with NO publication types at all.
        ([], True, "Imaging protocol variation across centres", EXCLUDED),
        # ...but the same title WITH publication types is kept: legitimate
        # studies get tagged, so the untagged heuristic must not fire.
        (["Journal Article"], True, "Imaging protocol variation across centres", STANDARD),
        # Priority publication types.
        (["Randomized Controlled Trial"], True, "A trial", PRIORITY),
        (["Meta-Analysis"], True, "A meta-analysis", PRIORITY),
        (["Systematic Review"], True, "A review", PRIORITY),
        (["Multicenter Study"], True, "A study", PRIORITY),
        (["Observational Study"], True, "A study", PRIORITY),
        (["Comparative Study"], True, "A study", PRIORITY),
        (["Clinical Trial"], True, "A trial", PRIORITY),
        (["Review"], True, "A review", PRIORITY),
        # Priority type but no abstract gets its own bucket.
        (["Randomized Controlled Trial"], False, "A trial", PRIORITY_NO_ABSTRACT),
        (["Meta-Analysis"], False, "A meta-analysis", PRIORITY_NO_ABSTRACT),
        # Generic types fall through on abstract availability.
        (["Journal Article"], True, "Some paper", STANDARD),
        (["Journal Article"], False, "Some paper", LOW_PRIORITY),
        ([], True, "Some paper", STANDARD),
        ([], False, "Some paper", LOW_PRIORITY),
    ],
)
def test_classify_article(pub_types, has_abstract, title, expected):
    assert classify_article(pub_types, has_abstract, title) == expected


def test_classify_is_case_sensitive_on_publication_types():
    """PubMed's publication types are a controlled vocabulary with fixed casing.

    Documenting this rather than asserting it is desirable: if we ever start
    seeing lowercased types from a new source, this test tells us the matching
    is exact and will silently miss them.
    """
    assert classify_article(["editorial"], True, "x") != EXCLUDED
    assert classify_article(["Editorial"], True, "x") == EXCLUDED


@pytest.mark.parametrize(
    ("pub_types", "title", "abstract", "expected"),
    [
        (["Randomized Controlled Trial"], "", "", True),
        (["randomised controlled trial"], "", "", True),
        (["Journal Article"], "A randomized controlled trial of X", "", True),
        (["Journal Article"], "", "Patients were randomly assigned to two arms", True),
        (["Journal Article"], "", "random assignment was used", True),
        (["Meta-Analysis"], "A meta-analysis", "pooled estimate", False),
        (["Journal Article"], "A cohort study", "observational data", False),
    ],
)
def test_is_rct(pub_types, title, abstract, expected):
    assert is_rct(pub_types, title, abstract) is expected


@pytest.mark.parametrize(
    ("pub_types", "title", "abstract", "expected"),
    [
        (["Randomized Controlled Trial"], "", "", True),
        (["Meta-Analysis"], "", "", True),
        (["Systematic Review"], "", "", True),
        (["Cohort Study"], "", "", True),
        (["Journal Article"], "A nationwide registry analysis", "", True),
        (["Journal Article"], "", "a population-based cohort study", True),
        (["Journal Article"], "A multicentre evaluation", "", True),
        (["Journal Article"], "A case report of one patient", "single case", False),
        # "Review" is a priority PUB type at ingest but not a priority STUDY
        # type at ranking. The two sets differ on purpose.
        (["Review"], "A narrative review", "", False),
    ],
)
def test_is_priority_study(pub_types, title, abstract, expected):
    assert is_priority_study(pub_types, title, abstract) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("META-ANALYSIS", "Meta-analysis"),
        ("meta-analysis", "Meta-analysis"),
        ("rct", "RCT"),
        ("RCT", "RCT"),
        ("prospective cohort", "Prospective cohort"),
        ("SYSTEMATIC REVIEW", "Systematic review"),
        ("pooled rct analysis", "Pooled RCT analysis"),
        ("", ""),
    ],
)
def test_normalize_study_type(raw, expected):
    assert normalize_study_type(raw) == expected
