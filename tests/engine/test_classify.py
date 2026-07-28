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


# The publication type arrives weeks to months after publication, so for a
# brand-new paper the title is the ONLY signal. Every title below is real, taken
# from the local database, where each was carrying publication_types
# ["Journal Article"] and nothing else.
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # --- naming variants the original four phrases never matched ---
        (
            "A Multidomain Lifestyle Intervention for Invasively Confirmed ANOCA: "
            "The SAMCRO Randomized Trial.",
            True,
        ),
        (
            "Multidomain Intervention for Growth in Term Small-for-Gestational-Age "
            "Infants: A Randomized Clinical Trial.",
            True,
        ),
        (
            "Safe Sleep Video Intervention via Text Messaging to Low-Income Families: "
            "The SMARTER Randomized Clinical Trial.",
            True,
        ),
        (
            "Digitally Enabled Quality Improvement Intervention and LDL-C Control in "
            "Atherosclerotic Cardiovascular Disease: The SAPPHIRE-LDL Cluster "
            "Randomized Clinical Trial.",
            True,
        ),
        (
            "Mechanical Thrombectomy in Ischemic Stroke With a Medium or Distal "
            "Arterial Occlusion: The DISCOUNT Randomized Clinical Trial.",
            True,
        ),
        # An intervening design word must not break the match.
        (
            "Cluster randomised feasibility trial of PRISM: the PRimary Care "
            "Individual Social Norms MSK Data Dashboard.",
            True,
        ),
        ("A randomised, double-blind, placebo-controlled trial of X", True),
        ("A randomised open-label trial of Y", True),
        # A prespecified follow-up of randomised participants is still randomised
        # evidence, and a reader seeing an RCT badge on it is not misled. Called
        # out explicitly because it is the closest call in this table.
        (
            "Treadmill Stress Test in Patients With Asymptomatic Severe Aortic "
            "Stenosis: A Prespecified Registry-Based Follow-Up of the EARLY TAVR "
            "Randomized Clinical Trial.",
            True,
        ),
        # --- aggregate designs: these REPORT ON trials, they are not trials ---
        # All three were already false positives before the widening, because
        # their abstracts describe the randomised trials they pooled. Widening
        # the title match without this guard would have made the class larger.
        (
            "Cost-Effectiveness of Biportal Endoscopic Spine Surgery Compared with "
            "Microscopic Surgery for Lumbar Degenerative Diseases: A Pooled Analysis "
            "of Two Randomized Controlled Trials.",
            False,
        ),
        (
            "Drug-coated balloons versus drug-eluting stents for de novo coronary "
            "lesions: a systematic review and meta-analysis of randomised trials.",
            False,
        ),
        ("Efficacy of X: a network meta-analysis of randomised controlled trials", False),
        # --- must stay negative ---
        ("A prospective cohort study of Y", False),
        ("Randomized patients were followed in the registry for five years", False),
    ],
)
def test_is_rct_from_title_alone_before_pubmed_indexes_the_type(title, expected):
    assert is_rct(["Journal Article"], title, "") is expected


def test_aggregate_design_guard_applies_to_the_title_not_the_abstract():
    """A trial's own abstract routinely says "randomised controlled trial".

    The guard has to key off the title, which states the paper's own design.
    Keying off the abstract would suppress the badge on most genuine RCTs.
    """
    assert is_rct(["Journal Article"], "The DISCOUNT Randomized Clinical Trial", "") is True
    assert (
        is_rct(
            ["Journal Article"],
            "The DISCOUNT Randomized Clinical Trial",
            "We compared this against a prior meta-analysis of similar trials.",
        )
        is True
    )


def test_explicit_rct_publication_type_beats_the_aggregate_guard():
    """If PubMed has actually tagged it, trust the controlled vocabulary."""
    assert is_rct(["Randomized Controlled Trial"], "A pooled analysis of trials", "") is True


# Every abstract below is real, from a paper that is NOT a trial. They are the
# reason the title pattern is not simply run against title + abstract: reviews,
# registry analyses and scientific statements all discuss other people's trials
# using the same noun phrase a trial uses to name itself. Only first-person
# method language distinguishes them.
@pytest.mark.parametrize(
    "abstract",
    [
        "no differences were identified in this large retrospective analysis; "
        "a randomized trial with long-term follow-up is necessary.",
        "adequately powered randomized trials (RCTs) are lacking.",
        "the emergence of contemporary randomized trials and large population "
        "analyses has reshaped practice.",
        "on the basis of randomized clinical trials, the Food and Drug "
        "Administration approved the regimen.",
        "we pooled twelve randomised controlled trials comparing the two devices.",
    ],
)
def test_discussing_other_peoples_trials_is_not_being_one(abstract):
    assert is_rct(["Journal Article"], "Some review of a topic", abstract) is False


@pytest.mark.parametrize(
    "abstract",
    [
        "Patients were randomly assigned to two arms.",
        "random assignment was stratified by centre.",
        "Participants were randomly allocated 1:1 to intervention or usual care.",
    ],
)
def test_first_person_method_language_still_identifies_a_trial(abstract):
    """A trial whose title does not name its design is recognised by its methods."""
    assert is_rct(["Journal Article"], "Effect of X on Y", abstract) is True


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
