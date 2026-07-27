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

RCT_TEXT_PHRASES = (
    "randomized controlled",
    "randomised controlled",
    "randomly assigned",
    "random assignment",
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
    """True for a randomised controlled trial specifically (drives the RCT badge)."""
    if _lower_set(pub_types) & RCT_PUB_TYPES:
        return True
    text_lower = f"{title} {abstract}".lower()
    return any(phrase in text_lower for phrase in RCT_TEXT_PHRASES)


def is_priority_study(pub_types: Iterable[str], title: str = "", abstract: str = "") -> bool:
    """True for high-value designs: RCTs, meta-analyses, reviews, large cohorts."""
    if _lower_set(pub_types) & PRIORITY_STUDY_TYPES:
        return True
    text_lower = f"{title} {abstract}".lower()
    return any(phrase in text_lower for phrase in PRIORITY_TEXT_PHRASES)


def normalize_study_type(study_type: str) -> str:
    """Normalise a model-supplied study type to sentence case, preserving acronyms."""
    if not study_type:
        return study_type
    acronyms = {"rct": "RCT", "rcts": "RCTs"}
    words = study_type.lower().split()
    result: list[str] = []
    for i, word in enumerate(words):
        if word in acronyms:
            result.append(acronyms[word])
        elif i == 0:
            result.append(word.capitalize())
        else:
            result.append(word)
    return " ".join(result)
