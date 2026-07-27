"""CLI behaviour, including the JSON round-trip between `fetch` and `summarise`."""

import json
from datetime import date

import pytest
import responses

from engine.cli import _as_dict, _from_dict, build_parser, main
from engine.pubmed.client import EUTILS_BASE
from engine.pubmed.parse import parse_efetch_response

ESEARCH = EUTILS_BASE + "esearch.fcgi"
EFETCH = EUTILS_BASE + "efetch.fcgi"


@pytest.fixture
def article(pubmed_xml):
    return parse_efetch_response(pubmed_xml("rct"))[0]


def test_round_trip_preserves_every_field(article):
    """`fetch -o file` then `summarise file` must not lose or corrupt anything."""
    restored = _from_dict(json.loads(json.dumps(_as_dict(article))))
    assert restored == article


def test_round_trip_handles_a_missing_publication_date(pubmed_xml):
    original = parse_efetch_response(pubmed_xml("medline_date"))[0]
    assert original.pub_date is None
    restored = _from_dict(json.loads(json.dumps(_as_dict(original))))
    assert restored == original
    assert restored.pub_date_raw == "2023 Nov-Dec"


def test_as_dict_is_json_serialisable_and_adds_the_url(article):
    data = _as_dict(article)
    json.dumps(data)  # must not raise
    assert data["url"] == "https://pubmed.ncbi.nlm.nih.gov/41662456/"
    assert data["entrez_date"] == "2026-02-09"
    assert data["journal"]["nlm_unique_id"] == "0147763"


def test_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_fetch_accepts_repeated_journals():
    args = build_parser().parse_args(
        ["fetch", "--journal", "Circulation", "--journal", "Heart", "--since", "3"]
    )
    assert args.journal == ["Circulation", "Heart"]
    assert args.since == 3


def test_fetch_without_ncbi_email_exits_with_a_message(monkeypatch, capsys):
    monkeypatch.delenv("NCBI_EMAIL", raising=False)
    assert main(["fetch", "--journal", "Circulation"]) == 2
    assert "NCBI_EMAIL" in capsys.readouterr().err


def test_summarise_without_an_api_key_exits_with_a_message(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "in.json"
    path.write_text("[]")
    assert main(["summarise", str(path)]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


@responses.activate
def test_fetch_writes_json_to_the_output_file(monkeypatch, tmp_path, pubmed_xml):
    monkeypatch.setenv("NCBI_EMAIL", "test@example.com")
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    responses.add(
        responses.POST,
        ESEARCH,
        body="<eSearchResult><Count>1</Count><QueryKey>1</QueryKey><WebEnv>W</WebEnv></eSearchResult>",
        status=200,
    )
    responses.add(responses.POST, EFETCH, body=pubmed_xml("rct"), status=200)

    out = tmp_path / "out.json"
    assert main(["fetch", "--journal", "Circulation", "--since", "7", "-o", str(out)]) == 0

    payload = json.loads(out.read_text())
    assert len(payload) == 1
    assert payload[0]["pmid"] == "41662456"


@responses.activate
def test_fetch_applies_the_specialty_filter(monkeypatch, tmp_path, pubmed_xml):
    monkeypatch.setenv("NCBI_EMAIL", "test@example.com")
    responses.add(
        responses.POST,
        ESEARCH,
        body="<eSearchResult><Count>1</Count><QueryKey>1</QueryKey><WebEnv>W</WebEnv></eSearchResult>",
        status=200,
    )
    responses.add(responses.POST, EFETCH, body=pubmed_xml("unrelated_general"), status=200)

    out = tmp_path / "out.json"
    exit_code = main(
        [
            "fetch",
            "--journal",
            "NEJM",
            "-o",
            str(out),
            "--specialty-keyword",
            "cardi*",
        ]
    )
    assert exit_code == 0
    # A melanoma trial must not survive a cardiology filter.
    assert json.loads(out.read_text()) == []


def test_summarise_with_the_stub_prints_notes(tmp_path, capsys, article):
    path = tmp_path / "in.json"
    path.write_text(json.dumps([_as_dict(article)]))

    assert main(["summarise", str(path), "--limit", "1", "--fake"]) == 0

    out = capsys.readouterr().out
    assert "STUDY TYPE" in out
    assert "SO WHAT" in out
    assert article.pmid in out


def test_explicit_date_range_overrides_since(monkeypatch, tmp_path):
    args = build_parser().parse_args(
        ["fetch", "--journal", "X", "--since-date", "2026-01-01", "--to", "2026-01-31"]
    )
    assert date.fromisoformat(args.since_date) == date(2026, 1, 1)
    assert date.fromisoformat(args.to) == date(2026, 1, 31)
