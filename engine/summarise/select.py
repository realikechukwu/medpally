"""Ordering for the summarisation worklist.

This is the legacy select_for_summary() with its cap removed. The old "top 10"
was an *email size* constraint — a digest can only hold so many cards — and
carrying it into a feed would silently cap the pool at ten papers a night.

The ordering itself is preserved exactly, and still matters: it decides what gets
summarised first when a per-run budget binds.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..classify import PRIORITY, STANDARD, is_priority_study
from ..pubmed.models import FetchedArticle

DEFAULT_MIN_ABSTRACT_CHARS = 200


def rank_summary_candidates(
    articles: Sequence[FetchedArticle],
    *,
    min_abstract_chars: int = DEFAULT_MIN_ABSTRACT_CHARS,
) -> list[FetchedArticle]:
    """Articles eligible for summarisation, best first.

    Order: priority study designs, then other priority-category articles, then
    standard ones. Anything without a usable abstract is dropped — there is
    nothing for the model to read.
    """
    priority_studies: list[FetchedArticle] = []
    other: list[FetchedArticle] = []

    for a in articles:
        if is_priority_study(a.publication_types, a.title, a.abstract):
            priority_studies.append(a)
        else:
            other.append(a)

    other_priority = [a for a in other if a.category == PRIORITY]
    standard = [a for a in other if a.category == STANDARD]

    ordered = priority_studies + other_priority + standard
    return [a for a in ordered if len(a.abstract) >= min_abstract_chars]
