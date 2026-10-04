"""Turn whatever a reader shares into one PubMed record.

A share arrives as some mix of a URL, free text and a page title — a PubMed
link, a journal's article page, a bare DOI or PMID pasted from a reference
list, or just the title Android hands over with the link. parse_reference pulls
out every identifier it can find, and find_article tries them strongest first:

    PMID  → fetched directly
    PMCID, DOI, PII → one exact-match search each
    title → a title-word search, accepted only if a result's title really is
            the same title, so a vague share can never save the wrong paper

The server never fetches the shared page itself. Publisher sites block bots,
and fetching arbitrary reader-supplied URLs from inside our network is a
standing invitation to request forgery; the identifiers in the link and the
title alongside it cover the journals readers actually share.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import unquote

from .models import FetchedArticle

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_PUBMED_URL = re.compile(
    r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/|"
    r"europepmc\.org/(?:article|abstract)/MED/)(\d{1,9})(?!\d)",
    re.IGNORECASE,
)
_PMID_LABEL = re.compile(r"\bPMID:?\s*(\d{1,9})(?!\d)", re.IGNORECASE)
_BARE_PMID = re.compile(r"^\s*(\d{1,9})\s*$")
_PMCID = re.compile(r"\b(PMC\d{4,9})(?!\d)", re.IGNORECASE)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
# What journal sites append after the DOI in their article URLs.
_DOI_URL_SUFFIX = re.compile(
    r"/(?:full|abstract|abs|pdf|epdf|epub|fulltext|summary|figures|references|"
    r"suppl_file|meta|html)(?:/.*)?$",
    re.IGNORECASE,
)
# Elsevier's publisher item identifier, written out in Lancet URLs
# ("PIIS0140-6736(23)01234-5") and compacted in ScienceDirect ones
# ("pii/S0140673623012345").
_PII = re.compile(
    r"(?:\bpii/|PII)(S\d{4}-?\d{3}[\dX]\(?\d{2}\)?\d{5}-?[\dX])",
    re.IGNORECASE,
)
# What browsers append to an article's title in the tab: " | NEJM",
# " - PubMed", " — The Lancet".
_TITLE_SEPARATORS = re.compile(r"\s+(?:\||\u2014|\u2013|::)\s+")
_TRAILING_SITE = re.compile(r"\s+-\s+[^-]{1,40}$")
# Quote marks a copied title may be wrapped in, straight and curly.
_QUOTES = " \"'\u201c\u201d\u2018\u2019"
# Words PubMed ignores in a title search, plus ones too common to narrow it.
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "against",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "between",
        "both",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "during",
        "each",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "more",
        "most",
        "no",
        "nor",
        "not",
        "of",
        "on",
        "or",
        "other",
        "over",
        "per",
        "should",
        "so",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "under",
        "up",
        "upon",
        "use",
        "used",
        "using",
        "versus",
        "via",
        "vs",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "whether",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "within",
        "without",
    }
)
TITLE_WORD_LIMIT = 12
TITLE_MATCH_THRESHOLD = 0.88


@dataclass(frozen=True, slots=True)
class Reference:
    """Every identifier found in a share, strongest first."""

    pmid: str = ""
    pmcid: str = ""
    doi: str = ""
    pii: str = ""
    title: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.pmid or self.pmcid or self.doi or self.pii or self.title)


class ArticleSource(Protocol):
    """The two PubMed calls lookup needs; PubMedClient provides both."""

    def search_pmids(self, term: str, *, retmax: int = 5, sort: str = "") -> list[str]: ...

    def efetch_by_pmid(self, pmids: Sequence[str]) -> Iterable[FetchedArticle]: ...


def parse_reference(text: str = "", title: str = "") -> Reference:
    """Pull identifiers out of shared text (URLs included) and a page title.

    `text` is everything the reader shared or pasted; `title` is the separate
    page title a share sheet sends alongside a link, used only as a last resort.
    """
    decoded = unquote(text or "").strip()
    pmid = _first_group(_PUBMED_URL, decoded) or _first_group(_PMID_LABEL, decoded)
    if not pmid:
        bare = _BARE_PMID.match(decoded)
        pmid = bare.group(1) if bare else ""

    pmcid = (_first_group(_PMCID, decoded) or "").upper()
    doi = _clean_doi(_first_group(_DOI, decoded) or "")
    pii = _normalise_pii(_first_group(_PII, decoded) or "")

    # Shared text that is not a link (a title copied from a reference list)
    # counts as a title too, when no explicit one came with it.
    loose_text = _URL.sub(" ", decoded).strip()
    candidate_title = clean_title(title) or (
        clean_title(loose_text) if not (pmid or pmcid or doi or pii) else ""
    )
    return Reference(pmid=pmid, pmcid=pmcid, doi=doi, pii=pii, title=candidate_title)


def clean_title(raw: str) -> str:
    """The article's own title out of a browser tab title, or "" if too thin."""
    raw = " ".join((raw or "").split())
    if not raw:
        return ""
    segments = [s for s in _TITLE_SEPARATORS.split(raw) if s.strip()]
    title = next((s for s in segments if len(s.split()) >= 3), segments[0] if segments else "")
    for _ in range(2):
        title = _TRAILING_SITE.sub("", title)
    title = title.strip(_QUOTES)
    return title if len(title) >= 20 and len(title.split()) >= 3 else ""


def title_search_term(title: str) -> str:
    """An all-words title search; PubMed's phrase index rarely holds whole titles."""
    words: list[str] = []
    for word in re.findall(r"[a-z0-9][a-z0-9-]*", title.lower()):
        word = word.strip("-")
        if not word or word in _STOPWORDS or word in words:
            continue
        if len(word) < 3 and not any(ch.isdigit() for ch in word):
            continue
        words.append(word)
    if len(words) < 3:
        return ""
    return " AND ".join(f"{word}[ti]" for word in words[:TITLE_WORD_LIMIT])


def title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalise_title(a), _normalise_title(b)).ratio()


def find_article(reference: Reference, source: ArticleSource) -> FetchedArticle | None:
    """The PubMed record a reference points at, or None if nothing matches."""
    if reference.pmid:
        return _first(source.efetch_by_pmid([reference.pmid]))

    exact_terms = []
    if reference.pmcid:
        exact_terms.append(f"{reference.pmcid}[pmcid]")
    if reference.doi:
        exact_terms.append(f'"{reference.doi}"[doi]')
    if reference.pii:
        exact_terms.append(f'"{reference.pii}"[aid]')
    for term in exact_terms:
        pmids = source.search_pmids(term, retmax=2)
        if pmids:
            article = _first(source.efetch_by_pmid(pmids[:1]))
            if article is not None:
                return article

    if reference.title:
        term = title_search_term(reference.title)
        if term:
            pmids = source.search_pmids(term, retmax=5, sort="relevance")
            if pmids:
                return _best_title_match(reference.title, source.efetch_by_pmid(pmids))
    return None


def _best_title_match(title: str, articles: Iterable[FetchedArticle]) -> FetchedArticle | None:
    scored = [(title_similarity(title, article.title), article) for article in articles]
    if not scored:
        return None
    score, best = max(scored, key=lambda pair: pair[0])
    return best if score >= TITLE_MATCH_THRESHOLD else None


def _first_group(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def _first(articles: Iterable[FetchedArticle]) -> FetchedArticle | None:
    return next(iter(articles), None)


def _clean_doi(doi: str) -> str:
    if not doi:
        return ""
    doi = re.split(r"[?#]", doi, maxsplit=1)[0]
    doi = _DOI_URL_SUFFIX.sub("", doi)
    doi = re.sub(r"\.pdf$", "", doi, flags=re.IGNORECASE)
    # Sentence punctuation after a pasted DOI. A closing bracket stays when
    # the DOI opened one itself, as Lancet DOIs do: 10.1016/S0140-6736(23)01234-5.
    while doi and (doi[-1] in ".,;:]}/" or (doi[-1] == ")" and doi.count(")") > doi.count("("))):
        doi = doi[:-1]
    return doi


def _normalise_pii(raw: str) -> str:
    """Write a PII the way PubMed indexes it: S0140-6736(23)01234-5."""
    if not raw:
        return ""
    chars = re.sub(r"[^0-9X]", "", raw[1:].upper())
    if len(chars) != 16:
        return ""
    return f"S{chars[0:4]}-{chars[4:8]}({chars[8:10]}){chars[10:15]}-{chars[15]}"


def _normalise_title(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", title.lower()).split())
