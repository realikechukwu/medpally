"""efetch XML -> FetchedArticle.

Field extraction is lifted from cardiology-feed's fetch_cardiology_pubmed.py and
its behaviour is pinned by tests/engine/test_parse.py against real captured
responses. Two things are new, both required by the plan:

* MeSH headings are parsed (the old script ignored them, because MeSH filtering
  happened inside the PubMed query instead of at ingest).
* The Entrez date is parsed, so the ingest window and the feed ordering can use
  the date PubMed indexed the record rather than the publication date.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

from ..classify import classify_article
from ..errors import ParseError
from .models import FetchedArticle, JournalIdentity

MONTH_ABBREVIATIONS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _text(elem: ET.Element | None) -> str:
    """Flattened text of an element, including any inline markup children.

    PubMed titles and abstracts contain <i>, <sub>, <sup> and similar, so
    ``.text`` alone silently truncates at the first tag.
    """
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def month_to_number(month: str) -> int:
    """Month name, abbreviation or number -> 1-12. Returns 0 when unrecognised."""
    month = month.strip()
    if month.isdigit():
        value = int(month)
        return value if 1 <= value <= 12 else 0
    return MONTH_ABBREVIATIONS.get(month[:3].lower(), 0)


def parse_pubdate_raw(article: ET.Element) -> str:
    """Publication date as a display string, matching the legacy format exactly.

    Preference order is ArticleDate (the electronic publication date) then the
    issue PubDate, degrading through Y-M-D / Y-M / Y to a bare MedlineDate
    string such as "2023 Nov-Dec".
    """
    y = article.findtext(".//ArticleDate/Year")
    m = article.findtext(".//ArticleDate/Month")
    d = article.findtext(".//ArticleDate/Day")
    if y and m and d:
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

    y = article.findtext(".//JournalIssue/PubDate/Year")
    m = article.findtext(".//JournalIssue/PubDate/Month")
    d = article.findtext(".//JournalIssue/PubDate/Day")
    if y and m and d:
        return f"{y}-{month_to_number(m):02d}-{d.zfill(2)}"
    if y and m:
        return f"{y}-{month_to_number(m):02d}"
    if y:
        return y

    return article.findtext(".//JournalIssue/PubDate/MedlineDate") or ""


def parse_pubdate(article: ET.Element) -> date | None:
    """Publication date as a real date, or None when only a year/range is known.

    Deliberately strict: a partial date is not silently widened to the first of
    the month, because pub_date is display-only and feed ordering uses the
    Entrez date instead.
    """
    raw = parse_pubdate_raw(article)
    parts = raw.split("-")
    if len(parts) == 3:
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            return None
    return None


def parse_entrez_date(article: ET.Element) -> date | None:
    """The date PubMed indexed this record (PubMedPubDate PubStatus="entrez").

    This is the honest "when did this appear" signal and what the ingest window
    keys on. Falls back to the pubmed status date, which is usually identical.
    """
    for status in ("entrez", "pubmed"):
        elem = article.find(f'.//PubMedPubDate[@PubStatus="{status}"]')
        if elem is None:
            continue
        y = elem.findtext("Year")
        m = elem.findtext("Month")
        d = elem.findtext("Day")
        if y and m and d:
            try:
                return date(int(y), month_to_number(m) or int(m), int(d))
            except ValueError:
                continue
    return None


def parse_abstract(article: ET.Element) -> str:
    """Abstract text, prefixing each section with its structured label."""
    abs_elems = article.findall(".//Abstract/AbstractText")
    if not abs_elems:
        return ""
    chunks: list[str] = []
    for a in abs_elems:
        label = a.attrib.get("Label") or a.attrib.get("NlmCategory") or ""
        txt = _text(a)
        if not txt:
            continue
        chunks.append(f"{label}: {txt}" if label else txt)
    return "\n".join(chunks).strip()


def parse_mesh_terms(article: ET.Element) -> tuple[str, ...]:
    """MeSH descriptor names, deduplicated and order-preserved.

    Descriptors only. Qualifiers ("mortality", "therapy", "methods") are
    subheadings that modify a descriptor rather than name a topic, so including
    them would add noise to relevance matching without ever helping — PubMed's
    own [MeSH] search behaves the same way.

    Empty for anything PubMed has not yet indexed, which is most papers in their
    first weeks. That lag is why relevance matching also reads title and
    abstract.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for elem in article.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
        name = _text(elem)
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            terms.append(name)
    return tuple(terms)


def parse_keywords(article: ET.Element) -> tuple[str, ...]:
    keywords: list[str] = []
    seen: set[str] = set()
    for elem in article.findall(".//KeywordList/Keyword"):
        name = _text(elem)
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            keywords.append(name)
    return tuple(keywords)


def parse_journal_identity(article: ET.Element) -> JournalIdentity:
    """Every journal identifier the record carries.

    ISSNs and the NLM unique ID are the reliable join keys; titles vary between
    records for the same journal ("Heart" vs "Heart (British Cardiac Society)").
    """
    issn_print = ""
    issn_electronic = ""
    for issn in article.findall(".//Journal/ISSN"):
        value = _text(issn)
        if issn.attrib.get("IssnType") == "Electronic":
            issn_electronic = issn_electronic or value
        else:
            issn_print = issn_print or value

    return JournalIdentity(
        title=_text(article.find(".//Journal/Title")),
        iso_abbreviation=_text(article.find(".//Journal/ISOAbbreviation")),
        medline_ta=_text(article.find(".//MedlineJournalInfo/MedlineTA")),
        nlm_unique_id=_text(article.find(".//MedlineJournalInfo/NlmUniqueID")),
        issn_print=issn_print,
        issn_electronic=issn_electronic,
        issn_linking=_text(article.find(".//MedlineJournalInfo/ISSNLinking")),
    )


def parse_authors(article: ET.Element, limit: int = 3) -> tuple[str, ...]:
    """First `limit` authors as "Lastname I", matching the legacy format."""
    authors: list[str] = []
    for author in article.findall(".//AuthorList/Author")[:limit]:
        last = author.findtext("LastName") or ""
        first = author.findtext("ForeName") or ""
        if last and first:
            authors.append(f"{last} {first[0]}")
        elif last:
            authors.append(last)
    return tuple(authors)


def parse_doi(article: ET.Element) -> str:
    for id_elem in article.findall(".//ArticleIdList/ArticleId"):
        if id_elem.attrib.get("IdType") == "doi" and id_elem.text:
            return id_elem.text.strip()
    return ""


def parse_publication_types(article: ET.Element) -> tuple[str, ...]:
    return tuple(
        pt.text.strip()
        for pt in article.findall(".//PublicationTypeList/PublicationType")
        if pt.text and pt.text.strip()
    )


def parse_article(article: ET.Element) -> FetchedArticle:
    """One <PubmedArticle> element -> FetchedArticle."""
    pmid = article.findtext(".//PMID") or ""
    title = _text(article.find(".//ArticleTitle"))
    abstract = parse_abstract(article)
    publication_types = parse_publication_types(article)

    return FetchedArticle(
        pmid=pmid,
        title=title,
        abstract=abstract,
        journal=parse_journal_identity(article),
        pub_date_raw=parse_pubdate_raw(article),
        pub_date=parse_pubdate(article),
        entrez_date=parse_entrez_date(article),
        doi=parse_doi(article),
        publication_types=publication_types,
        mesh_terms=parse_mesh_terms(article),
        authors=parse_authors(article),
        category=classify_article(publication_types, bool(abstract), title),
        keywords=parse_keywords(article),
    )


def parse_efetch_response(xml_bytes: bytes) -> list[FetchedArticle]:
    """Full efetch response -> articles, skipping records with no PMID."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ParseError(f"efetch returned unparseable XML: {exc}") from exc

    articles = [parse_article(elem) for elem in root.findall(".//PubmedArticle")]
    return [a for a in articles if a.pmid]
