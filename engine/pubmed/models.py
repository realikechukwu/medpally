"""Value objects passed between the engine and its callers.

Frozen dataclasses rather than dicts: the Django adapter in
apps/ingestion/services.py consumes these, and a typo in a dict key there would
otherwise be silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class JournalIdentity:
    """Everything PubMed tells us about which journal an article came from.

    Kept separate from FetchedArticle because journal resolution (matching this
    against a Journal row) is a distinct step with its own failure mode.
    """

    title: str = ""
    iso_abbreviation: str = ""
    medline_ta: str = ""
    nlm_unique_id: str = ""
    issn_print: str = ""
    issn_electronic: str = ""
    issn_linking: str = ""

    @property
    def best_name(self) -> str:
        return self.title or self.medline_ta or self.iso_abbreviation


@dataclass(frozen=True, slots=True)
class FetchedArticle:
    """One article as parsed from an efetch response."""

    pmid: str
    title: str
    abstract: str
    journal: JournalIdentity
    pub_date_raw: str
    pub_date: date | None
    entrez_date: date | None
    doi: str = ""
    publication_types: tuple[str, ...] = ()
    mesh_terms: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    category: str = ""
    keywords: tuple[str, ...] = field(default=())

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else ""

    @property
    def has_abstract(self) -> bool:
        return bool(self.abstract)
