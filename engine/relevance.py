"""Specialty relevance matching.

This replaces the old build_general_journal_cardiology_query(), which pushed a
MeSH/title filter into the PubMed query itself. Matching in Python at ingest
instead buys three things:

* Adding a specialty later backfills over papers already in the database — no
  refetch, no PubMed quota, instant back-catalogue.
* The rules can be tuned and re-run over history.
* It survives MeSH indexing lag. MeSH headings arrive weeks to months after
  publication, so a brand-new NEJM cardiology paper has none. Under the old
  query-time filter that paper was never seen, and once the search window moved
  on it was never seen again. Here it can match on title or abstract now, and
  be re-checked later when its MeSH lands.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

JOURNAL_SCOPE = "journal_scope"
TOPICAL_MATCH = "topical_match"


@dataclass(frozen=True, slots=True)
class SpecialtyRules:
    """The matching vocabulary for one specialty."""

    slug: str
    mesh_terms: tuple[str, ...] = ()
    title_keywords: tuple[str, ...] = ()
    abstract_keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MatchResult:
    matched: bool
    matched_mesh: tuple[str, ...] = ()
    matched_keywords: tuple[str, ...] = ()


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Whole-word/phrase containment, with an optional trailing-stem wildcard.

    Plain substring matching is not an option: it would let "ace" match
    "surface" and "mi" match "family", which in a clinical vocabulary is not a
    hypothetical problem.

    But strict whole-word matching is too tight at the other end. Against live
    NEJM output, "cardiac" failed to match "Cardiotoxic Effects after Gene
    Therapy" — a paper a cardiologist wants. A vocabulary term may therefore end
    in "*" to match a word stem: "cardi*" covers cardiac, cardiovascular,
    cardiotoxic, cardiomyopathy and carditis in one entry.
    """
    if not needle:
        return False
    if needle.endswith("*"):
        stem = needle[:-1]
        if not stem:
            return False
        pattern = r"(?<!\w)" + re.escape(stem) + r"\w*"
    else:
        pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
    return re.search(pattern, haystack) is not None


def match_specialty(
    *,
    rules: SpecialtyRules,
    mesh_terms: Sequence[str] = (),
    title: str = "",
    abstract: str = "",
) -> MatchResult:
    """Decide whether an article is topically relevant to a specialty.

    MeSH headings match exactly (they are a controlled vocabulary, so a
    substring test would be both unnecessary and wrong). Title and abstract
    keywords match as whole phrases.
    """
    article_mesh = {_normalize(m) for m in mesh_terms if m}
    matched_mesh = tuple(term for term in rules.mesh_terms if _normalize(term) in article_mesh)

    title_norm = _normalize(title)
    matched_keywords = [
        kw for kw in rules.title_keywords if _contains_phrase(title_norm, _normalize(kw))
    ]

    # Abstract keywords are a deliberately separate, usually narrower list: the
    # abstract is long enough that a loose term produces false positives the
    # title never would.
    if rules.abstract_keywords and abstract:
        abstract_norm = _normalize(abstract)
        seen = {kw.lower() for kw in matched_keywords}
        matched_keywords.extend(
            kw
            for kw in rules.abstract_keywords
            if kw.lower() not in seen and _contains_phrase(abstract_norm, _normalize(kw))
        )

    return MatchResult(
        matched=bool(matched_mesh or matched_keywords),
        matched_mesh=matched_mesh,
        matched_keywords=tuple(matched_keywords),
    )
