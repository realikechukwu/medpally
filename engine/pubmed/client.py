"""E-utilities client.

Uses ESearch's history server: the search stores its result set server-side and
returns a WebEnv/QueryKey pair, then EFetch pulls batches straight off that
history rather than round-tripping an ID list. This removes the old script's
hard `retmax=300` ceiling, which would have started silently dropping papers as
the journal set grew.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date

from ..errors import ParseError
from ..http import RATE_WITH_KEY, RATE_WITHOUT_KEY, HttpClient
from .models import FetchedArticle
from .parse import parse_efetch_response
from .queries import build_search_term

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
EFETCH_BATCH_SIZE = 200


@dataclass(frozen=True, slots=True)
class SearchResult:
    count: int
    web_env: str
    query_key: str


class PubMedClient:
    def __init__(
        self,
        *,
        email: str,
        api_key: str = "",
        tool: str = "medpally",
        http: HttpClient | None = None,
    ) -> None:
        self.email = email
        self.api_key = api_key
        self.tool = tool
        self.http = http or HttpClient(
            rate_per_second=RATE_WITH_KEY if api_key else RATE_WITHOUT_KEY,
            user_agent=f"{tool}/1.0 (+mailto:{email})",
        )

    def _base_params(self) -> dict[str, str]:
        params = {"db": "pubmed", "tool": self.tool, "email": self.email}
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def esearch(self, term: str) -> SearchResult:
        """Run a search and keep the result set on the history server."""
        params = self._base_params() | {"term": term, "retmode": "xml", "usehistory": "y"}
        raw = self.http.post(EUTILS_BASE + "esearch.fcgi", params)

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ParseError(f"esearch returned unparseable XML: {exc}") from exc

        error = root.findtext("ERROR")
        if error:
            raise ParseError(f"esearch rejected the query: {error}")

        web_env = root.findtext("WebEnv")
        query_key = root.findtext("QueryKey")
        if not web_env or not query_key:
            raise ParseError("esearch returned no WebEnv/QueryKey")

        return SearchResult(
            count=int(root.findtext("Count") or "0"),
            web_env=web_env,
            query_key=query_key,
        )

    def efetch_from_history(
        self, result: SearchResult, *, batch_size: int = EFETCH_BATCH_SIZE
    ) -> Iterator[FetchedArticle]:
        """Stream every article in a stored result set."""
        for retstart in range(0, result.count, batch_size):
            params = self._base_params() | {
                "retmode": "xml",
                "WebEnv": result.web_env,
                "query_key": result.query_key,
                "retstart": str(retstart),
                "retmax": str(batch_size),
            }
            raw = self.http.post(EUTILS_BASE + "efetch.fcgi", params)
            yield from parse_efetch_response(raw)

    def efetch_by_pmid(
        self, pmids: Sequence[str], *, batch_size: int = EFETCH_BATCH_SIZE
    ) -> Iterator[FetchedArticle]:
        """Fetch specific PMIDs. Used for backfill and for re-checking a record."""
        for i in range(0, len(pmids), batch_size):
            params = self._base_params() | {
                "retmode": "xml",
                "id": ",".join(pmids[i : i + batch_size]),
            }
            raw = self.http.post(EUTILS_BASE + "efetch.fcgi", params)
            yield from parse_efetch_response(raw)

    def fetch_journal_window(
        self, journals: Sequence[str], date_from: date, date_to: date
    ) -> Iterator[FetchedArticle]:
        """Every article from these journals indexed in [date_from, date_to]."""
        term = build_search_term(journals, date_from, date_to)
        result = self.esearch(term)
        logger.info(
            "esearch matched %d records across %d journals (%s..%s)",
            result.count,
            len(journals),
            date_from,
            date_to,
        )
        if result.count == 0:
            return
        yield from self.efetch_from_history(result)
