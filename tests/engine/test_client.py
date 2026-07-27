"""HTTP boundary. Every response is mocked — these tests never touch the network."""

from datetime import date

import pytest
import responses

from engine.errors import ParseError, RateLimitError, TransportError
from engine.http import HttpClient, TokenBucket
from engine.pubmed.client import EUTILS_BASE, PubMedClient

ESEARCH = EUTILS_BASE + "esearch.fcgi"
EFETCH = EUTILS_BASE + "efetch.fcgi"

SEARCH_OK = """<?xml version="1.0"?>
<eSearchResult><Count>3</Count><RetMax>3</RetMax>
<QueryKey>1</QueryKey><WebEnv>MCID_abc123</WebEnv>
<IdList><Id>1</Id><Id>2</Id><Id>3</Id></IdList></eSearchResult>"""

SEARCH_EMPTY = """<?xml version="1.0"?>
<eSearchResult><Count>0</Count><QueryKey>1</QueryKey><WebEnv>MCID_x</WebEnv>
<IdList/></eSearchResult>"""


def article(pmid: str) -> str:
    return f"""<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID>
    <Article><Journal><Title>Circulation</Title></Journal>
    <ArticleTitle>Paper {pmid}</ArticleTitle>
    <Abstract><AbstractText>Body of {pmid}.</AbstractText></Abstract>
    <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
    </Article></MedlineCitation></PubmedArticle>"""


def fetch_body(*pmids: str) -> str:
    return "<PubmedArticleSet>" + "".join(article(p) for p in pmids) + "</PubmedArticleSet>"


@pytest.fixture
def client():
    # A high rate keeps the token bucket from actually sleeping in tests.
    http = HttpClient(rate_per_second=1000.0, user_agent="test/1.0", max_attempts=3)
    return PubMedClient(email="test@example.com", tool="medfeed-test", http=http)


# ---------------------------------------------------------------- token bucket


def test_token_bucket_allows_a_burst_up_to_capacity():
    bucket = TokenBucket(rate_per_second=10.0, capacity=3)
    for _ in range(3):
        bucket.acquire()  # should not block


def test_token_bucket_refills_over_time(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("engine.http.time.sleep", slept.append)
    bucket = TokenBucket(rate_per_second=2.0, capacity=1)
    bucket.acquire()
    bucket.acquire()
    assert slept, "second acquire should have waited for a refill"


# ---------------------------------------------------------------- esearch


@responses.activate
def test_esearch_returns_history_handles(client):
    responses.add(responses.POST, ESEARCH, body=SEARCH_OK, status=200)
    result = client.esearch("anything")
    assert (result.count, result.web_env, result.query_key) == (3, "MCID_abc123", "1")


@responses.activate
def test_esearch_sends_post_with_usehistory(client):
    responses.add(responses.POST, ESEARCH, body=SEARCH_OK, status=200)
    client.esearch('("Circulation"[jour])')
    body = responses.calls[0].request.body
    assert "usehistory=y" in body
    assert "db=pubmed" in body
    # POST, so the query never rides in the URL and has no length ceiling.
    assert "?" not in responses.calls[0].request.url


@responses.activate
def test_esearch_surfaces_an_upstream_error_element(client):
    responses.add(
        responses.POST,
        ESEARCH,
        body="<eSearchResult><ERROR>Invalid field</ERROR></eSearchResult>",
        status=200,
    )
    with pytest.raises(ParseError, match="rejected the query"):
        client.esearch("bad[[[")


@responses.activate
def test_esearch_rejects_a_response_with_no_webenv(client):
    responses.add(
        responses.POST, ESEARCH, body="<eSearchResult><Count>5</Count></eSearchResult>", status=200
    )
    with pytest.raises(ParseError, match="WebEnv"):
        client.esearch("x")


@responses.activate
def test_malformed_search_xml_raises_parse_error(client):
    responses.add(responses.POST, ESEARCH, body="<eSearchResult><Count>", status=200)
    with pytest.raises(ParseError, match="unparseable"):
        client.esearch("x")


# ---------------------------------------------------------------- efetch


@responses.activate
def test_fetch_journal_window_pages_through_the_history(client):
    responses.add(responses.POST, ESEARCH, body=SEARCH_OK, status=200)
    responses.add(responses.POST, EFETCH, body=fetch_body("1", "2"), status=200)
    responses.add(responses.POST, EFETCH, body=fetch_body("3"), status=200)

    articles = list(client.efetch_from_history(client.esearch("x"), batch_size=2))
    assert [a.pmid for a in articles] == ["1", "2", "3"]

    second = responses.calls[2].request.body
    assert "retstart=2" in second
    assert "WebEnv=MCID_abc123" in second


@responses.activate
def test_empty_search_never_calls_efetch(client):
    responses.add(responses.POST, ESEARCH, body=SEARCH_EMPTY, status=200)
    articles = list(
        client.fetch_journal_window(["Circulation"], date(2026, 7, 1), date(2026, 7, 7))
    )
    assert articles == []
    assert len(responses.calls) == 1


@responses.activate
def test_efetch_by_pmid_batches(client):
    responses.add(responses.POST, EFETCH, body=fetch_body("1", "2"), status=200)
    responses.add(responses.POST, EFETCH, body=fetch_body("3"), status=200)
    articles = list(client.efetch_by_pmid(["1", "2", "3"], batch_size=2))
    assert [a.pmid for a in articles] == ["1", "2", "3"]
    assert "id=1%2C2" in responses.calls[0].request.body


# ---------------------------------------------------------------- retries


@responses.activate
def test_retries_a_429_then_succeeds(client, monkeypatch):
    monkeypatch.setattr("engine.http.time.sleep", lambda _: None)
    responses.add(responses.POST, ESEARCH, body="rate limited", status=429)
    responses.add(responses.POST, ESEARCH, body=SEARCH_OK, status=200)

    assert client.esearch("x").count == 3
    assert len(responses.calls) == 2


@responses.activate
def test_retries_a_503_then_succeeds(client, monkeypatch):
    monkeypatch.setattr("engine.http.time.sleep", lambda _: None)
    responses.add(responses.POST, ESEARCH, body="unavailable", status=503)
    responses.add(responses.POST, ESEARCH, body=SEARCH_OK, status=200)
    assert client.esearch("x").count == 3


@responses.activate
def test_persistent_429_raises_rate_limit_error(client, monkeypatch):
    monkeypatch.setattr("engine.http.time.sleep", lambda _: None)
    for _ in range(3):
        responses.add(responses.POST, ESEARCH, body="slow down", status=429)
    with pytest.raises(RateLimitError):
        client.esearch("x")


@responses.activate
def test_retry_after_header_is_honoured(client, monkeypatch):
    delays: list[float] = []
    monkeypatch.setattr("engine.http.time.sleep", delays.append)
    responses.add(responses.POST, ESEARCH, status=429, headers={"Retry-After": "7"})
    responses.add(responses.POST, ESEARCH, body=SEARCH_OK, status=200)
    client.esearch("x")
    assert 7.0 in delays


@responses.activate
def test_a_400_is_not_retried(client):
    responses.add(responses.POST, ESEARCH, body="bad request", status=400)
    with pytest.raises(TransportError, match="HTTP 400"):
        client.esearch("x")
    assert len(responses.calls) == 1


@responses.activate
def test_connection_errors_are_retried_then_reported(client, monkeypatch):
    # requests raises its own ConnectionError subclass, not the builtin.
    from requests.exceptions import ConnectionError as RequestsConnectionError

    monkeypatch.setattr("engine.http.time.sleep", lambda _: None)
    for _ in range(3):
        responses.add(responses.POST, ESEARCH, body=RequestsConnectionError("boom"))
    with pytest.raises(TransportError, match="after 3 attempts"):
        client.esearch("x")
    assert len(responses.calls) == 3


@responses.activate
def test_a_transient_connection_error_recovers(client, monkeypatch):
    from requests.exceptions import ConnectionError as RequestsConnectionError

    monkeypatch.setattr("engine.http.time.sleep", lambda _: None)
    responses.add(responses.POST, ESEARCH, body=RequestsConnectionError("blip"))
    responses.add(responses.POST, ESEARCH, body=SEARCH_OK, status=200)
    assert client.esearch("x").count == 3


def test_api_key_raises_the_rate_limit():
    from engine.http import RATE_WITH_KEY, RATE_WITHOUT_KEY

    with_key = PubMedClient(email="a@b.c", api_key="secret")
    without = PubMedClient(email="a@b.c")
    assert with_key.http.bucket.rate == RATE_WITH_KEY
    assert without.http.bucket.rate == RATE_WITHOUT_KEY


@responses.activate
def test_api_key_is_sent_when_present():
    http = HttpClient(rate_per_second=1000.0, user_agent="t/1.0")
    client = PubMedClient(email="a@b.c", api_key="sekrit", http=http)
    responses.add(responses.POST, ESEARCH, body=SEARCH_OK, status=200)
    client.esearch("x")
    assert "api_key=sekrit" in responses.calls[0].request.body
