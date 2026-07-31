"""Publication-type classification.

Lifted verbatim from cardiology-feed's fetch_cardiology_pubmed.py and
summarise_and_email.py. This logic is earned — it was tuned against real digest
output over many weeks — so it is copied rather than rewritten, and the tests in
tests/engine/test_classify.py pin its exact behaviour.

Note the two constant sets are deliberately different and always were:
PRIORITY_PUB_TYPES (used at ingest, to decide what is worth keeping) includes
"Review"; PRIORITY_STUDY_TYPES (used at ranking, to decide what to summarise
first) does not, but adds "cohort study".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

# Publication types to prioritise (original research and reviews).
PRIORITY_PUB_TYPES = frozenset(
    {
        "Clinical Trial",
        "Randomized Controlled Trial",
        "Multicenter Study",
        "Meta-Analysis",
        "Systematic Review",
        "Observational Study",
        "Comparative Study",
        "Review",
    }
)

# Publication types to exclude (non-substantive content).
EXCLUDE_PUB_TYPES = frozenset(
    {
        "Editorial",
        "Comment",
        "Letter",
        "News",
        "Published Erratum",
        "Retraction of Publication",
        "Retracted Publication",
        "Clinical Study Protocol",
        "Clinical Trial Protocol",
        "Study Protocol",
    }
)

PROTOCOL_TITLE_PHRASES = (
    "study protocol",
    "trial protocol",
    "protocol for",
    ": protocol",
    "protocol of",
    "research protocol",
)

# Lowercased publication types that mark a study worth summarising first.
PRIORITY_STUDY_TYPES = frozenset(
    {
        "randomized controlled trial",
        "randomised controlled trial",
        "clinical trial",
        "meta-analysis",
        "systematic review",
        "multicenter study",
        "observational study",
        "cohort study",
    }
)

RCT_PUB_TYPES = frozenset({"randomized controlled trial", "randomised controlled trial"})

# Titles that describe an aggregate design. These papers report ON randomised
# trials without being one, and they say so in their abstracts ("we pooled 12
# randomised controlled trials..."), which is exactly what the text heuristic
# below keys on. Checked against the TITLE only: a paper's title states its own
# design, whereas a genuine trial's abstract routinely mentions other trials.
AGGREGATE_DESIGN_TITLE_PHRASES = (
    "meta-analysis",
    "meta analysis",
    "metaanalysis",
    "systematic review",
    "scoping review",
    "umbrella review",
    "pooled analysis",
)

# Title and abstract carry different evidence and are matched differently.
#
# A TITLE names the paper's own design, so a naming variant in it is diagnostic:
#
#   randomized trial · randomized clinical trial · cluster randomised
#   feasibility trial · randomised, double-blind, placebo-controlled trial
#
# Up to two intervening design words, deliberately. Enough for every variant in
# the corpus, narrow enough to leave prose alone — "randomized patients were
# followed in the trial" needs three and does not match.
RCT_TITLE_PATTERN = re.compile(
    r"\brandomi[sz]ed\b(?:[\s,]+[\w-]+){0,2}?[\s,]+trials?\b"
    r"|\brandomi[sz]ed[\s-]+controlled\b"
)

# An ABSTRACT, by contrast, routinely discusses OTHER people's trials, and the
# noun phrase "randomised trial" is what reviews and registry papers use to do
# it: "adequately powered randomized trials are lacking", "on the basis of
# randomized clinical trials, the FDA...". Matching that phrase in an abstract
# flags reviews as trials.
#
# So abstracts are matched only on first-person method language — a sentence
# that can only be describing what THIS paper did to ITS participants.
RCT_ABSTRACT_PATTERN = re.compile(
    r"\brandomly assigned\b|\brandom assignment\b|\brandomly allocated\b"
)

PRIORITY_TEXT_PHRASES = (
    "randomized",
    "randomised",
    "meta-analysis",
    "meta analysis",
    "systematic review",
    "cohort study",
    "multicenter",
    "multicentre",
    "registry",
    "nationwide",
    "population-based",
)

# Categories, as returned by classify_article.
EXCLUDED = "excluded"
PRIORITY = "priority"
PRIORITY_NO_ABSTRACT = "priority_no_abstract"
STANDARD = "standard"
LOW_PRIORITY = "low_priority"

# The categories that are eligible to reach a user's feed.
DIGEST_CATEGORIES = frozenset({PRIORITY, STANDARD})


def classify_article(pub_types: Sequence[str], has_abstract: bool, title: str = "") -> str:
    """Classify an article by publication type, abstract availability and title."""
    pub_types_set = set(pub_types)

    if pub_types_set & EXCLUDE_PUB_TYPES:
        return EXCLUDED

    title_lower = title.lower()

    # A paper with "protocol" in the title and no publication types at all is
    # almost always an untagged protocol. Legitimate studies get tagged.
    if "protocol" in title_lower and not pub_types:
        return EXCLUDED

    if any(phrase in title_lower for phrase in PROTOCOL_TITLE_PHRASES):
        return EXCLUDED

    if pub_types_set & PRIORITY_PUB_TYPES:
        return PRIORITY if has_abstract else PRIORITY_NO_ABSTRACT

    if has_abstract:
        return STANDARD

    return LOW_PRIORITY


def _lower_set(pub_types: Iterable[str]) -> set[str]:
    return {pt.lower().strip() for pt in pub_types}


def is_rct(pub_types: Iterable[str], title: str = "", abstract: str = "") -> bool:
    """True for a randomised controlled trial specifically (drives the RCT badge).

    The publication type is authoritative when present, but it arrives weeks to
    months after publication — the same indexing lag that affects MeSH terms.
    For a brand-new paper the title is the only signal there is, so it has to
    recognise how trials are actually named rather than one canonical spelling.
    """
    if _lower_set(pub_types) & RCT_PUB_TYPES:
        return True

    # Only reached when PubMed has not tagged the type, so an aggregate design
    # can only be spotted from the title.
    title_lower = title.lower()
    if any(phrase in title_lower for phrase in AGGREGATE_DESIGN_TITLE_PHRASES):
        return False

    if RCT_TITLE_PATTERN.search(title_lower):
        return True

    return bool(RCT_ABSTRACT_PATTERN.search(abstract.lower()))


def is_priority_study(pub_types: Iterable[str], title: str = "", abstract: str = "") -> bool:
    """True for high-value designs: RCTs, meta-analyses, reviews, large cohorts."""
    if _lower_set(pub_types) & PRIORITY_STUDY_TYPES:
        return True
    text_lower = f"{title} {abstract}".lower()
    return any(phrase in text_lower for phrase in PRIORITY_TEXT_PHRASES)


CANONICAL_STUDY_TYPES = frozenset({
    "RCT", "Meta-analysis", "Systematic review", "Guideline", "Prospective cohort",
    "Retrospective cohort", "Case-control", "Case series", "Narrative review", "Other",
})
_STUDY_TYPE_ALIASES = {
    "rct": "RCT", "randomized controlled trial": "RCT", "randomised controlled trial": "RCT",
    "randomized trial": "RCT", "randomised trial": "RCT", "meta analysis": "Meta-analysis",
    "meta-analysis": "Meta-analysis", "systematic review": "Systematic review",
    "clinical practice guideline": "Guideline", "guideline": "Guideline",
    "prospective cohort": "Prospective cohort", "prospective cohort study": "Prospective cohort",
    "retrospective cohort": "Retrospective cohort", "retrospective cohort study": "Retrospective cohort",
    "case control": "Case-control", "case-control": "Case-control", "case series": "Case series",
    "narrative review": "Narrative review", "other": "Other",
}


def normalize_study_type(study_type: str) -> str:
    """Snap model-supplied free text to the finite editorial design vocabulary."""
    normalized = " ".join(study_type.lower().strip().split())
    if not normalized:
        return ""
    return _STUDY_TYPE_ALIASES.get(normalized, "Other")
