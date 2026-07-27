"""E-utilities query construction.

The one substantive change from cardiology-feed: the date window is built on
[edat] (Entrez date — when PubMed indexed the record) rather than [dp]
(publication date).

That was a real bug. Journals that publish online-ahead-of-print, or that assign
a forward-dated issue, can have a publication date weeks or months away from the
day the record actually appeared. Windowing on [dp] therefore both misses new
papers and re-surfaces old ones. Publication date is still parsed and stored, but
only for display.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

# Chunk size for journal OR-lists. Not a URL-length constraint (we POST), but
# fault isolation: one malformed journal name can only poison its own batch, and
# per-batch hit counts tell you which 40 journals to look at when a count is 0.
JOURNAL_CHUNK_SIZE = 40


def build_journal_query(journals: Sequence[str]) -> str:
    """OR-list of journal terms: ("Circulation"[jour] OR "Heart"[jour])."""
    if not journals:
        raise ValueError("build_journal_query requires at least one journal")
    parts = [f'"{j}"[jour]' for j in journals]
    return "(" + " OR ".join(parts) + ")"


def build_date_clause(date_from: date, date_to: date) -> str:
    """Entrez-date window. Inclusive at both ends."""
    if date_from > date_to:
        raise ValueError(f"date_from {date_from} is after date_to {date_to}")
    return f'("{date_from:%Y/%m/%d}"[edat] : "{date_to:%Y/%m/%d}"[edat])'


def build_search_term(journals: Sequence[str], date_from: date, date_to: date) -> str:
    return f"{build_journal_query(journals)} AND {build_date_clause(date_from, date_to)}"


def chunked(items: Sequence[str], size: int = JOURNAL_CHUNK_SIZE) -> list[list[str]]:
    if size < 1:
        raise ValueError("chunk size must be >= 1")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def normalize_journal_name(name: str) -> str:
    """Key for matching journal-name variants against each other.

    Lowercases, drops a leading "the", and strips punctuation and whitespace
    runs, so that "The Lancet", "Lancet" and "lancet" collapse together, as do
    "JACC: Cardiovascular Imaging" and "JACC Cardiovascular Imaging".
    """
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in name.lower())
    words: Iterable[str] = cleaned.split()
    parts = list(words)
    if parts and parts[0] == "the":
        parts = parts[1:]
    return " ".join(parts)
