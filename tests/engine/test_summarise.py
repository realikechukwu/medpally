"""Summarisation: schema conformance, request shape, and worklist ordering."""

import json
from types import SimpleNamespace

import jsonschema
import pytest

from engine.errors import SummariserError
from engine.pubmed.models import FetchedArticle, JournalIdentity
from engine.pubmed.parse import parse_efetch_response
from engine.summarise.client import (
    DEFAULT_TEMPERATURE,
    FakeSummariser,
    OpenAISummariser,
    supports_temperature,
)
from engine.summarise.prompt import PROMPT_VERSION, build_system_prompt, build_user_prompt
from engine.summarise.schema import SUMMARY_SCHEMA, EditorialNote
from engine.summarise.select import rank_summary_candidates

VALID_PAYLOAD = {
    "study_type": "RCT",
    "context": "Whether anticoagulation reduces stroke in device-detected AF.",
    "finding": "Apixaban reduced stroke (HR 0.63, 95% CI 0.45-0.88, p=0.007).",
    "so_what": "Supports anticoagulation in selected patients with subclinical AF.",
    "tags": ["Atrial Fibrillation", "Anticoagulation"],
}


def make_article(**kwargs) -> FetchedArticle:
    defaults = {
        "pmid": "1",
        "title": "A title",
        "abstract": "x" * 500,
        "journal": JournalIdentity(title="Circulation"),
        "pub_date_raw": "2026-07-01",
        "pub_date": None,
        "entrez_date": None,
        "publication_types": ("Journal Article",),
        "category": "standard",
    }
    return FetchedArticle(**{**defaults, **kwargs})


# ---------------------------------------------------------------- schema


def test_valid_payload_conforms_to_the_schema():
    jsonschema.validate(VALID_PAYLOAD, SUMMARY_SCHEMA["schema"])


@pytest.mark.parametrize(
    "mutation",
    [
        {"study_type": None},
        {"extra_field": "not allowed"},
        {"tags": ["a", "b", "c", "d", "e"]},  # maxItems is 4
    ],
)
def test_schema_rejects_malformed_payloads(mutation):
    payload = {**VALID_PAYLOAD, **mutation}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SUMMARY_SCHEMA["schema"])


def test_schema_requires_every_field():
    for field in ("study_type", "context", "finding", "so_what", "tags"):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != field}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, SUMMARY_SCHEMA["schema"])


def test_schema_is_strict():
    assert SUMMARY_SCHEMA["strict"] is True
    assert SUMMARY_SCHEMA["schema"]["additionalProperties"] is False


def test_editorial_note_normalises_the_study_type():
    note = EditorialNote.from_payload({**VALID_PAYLOAD, "study_type": "META-ANALYSIS"})
    assert note.study_type == "Meta-analysis"


def test_editorial_note_drops_blank_tags():
    note = EditorialNote.from_payload({**VALID_PAYLOAD, "tags": ["Real", "  ", ""]})
    assert note.tags == ("Real",)


# ---------------------------------------------------------------- prompt


def test_system_prompt_is_specialty_parameterised():
    assert "cardiology digest" in build_system_prompt("Cardiology")
    assert "spine surgery digest" in build_system_prompt("Spine Surgery")


def test_system_prompt_keeps_its_editorial_constraints():
    """These clauses are the product voice; losing one silently changes output."""
    prompt = build_system_prompt("cardiology")
    for clause in (
        "exactly five fields",
        "Include effect size, CI, and p-value if reported",
        "write 'Not reported'",
        "No hype words",
        "Use only information from the provided abstract",
    ):
        assert clause in prompt


def test_user_prompt_shape():
    prompt = build_user_prompt(
        title="T",
        journal="J",
        pub_date="2026-07-01",
        publication_types=("Journal Article", "Review"),
        abstract="A",
    )
    assert prompt.startswith("TITLE: T\nJOURNAL: J\nPUB DATE: 2026-07-01\n")
    assert "PUBLICATION TYPES: Journal Article, Review" in prompt


def test_user_prompt_handles_no_publication_types():
    prompt = build_user_prompt(
        title="T", journal="J", pub_date="", publication_types=(), abstract="A"
    )
    assert "PUBLICATION TYPES: Not specified" in prompt


# ---------------------------------------------------------------- OpenAI client


class StubOpenAI:
    """Minimal stand-in recording the kwargs the summariser sends."""

    def __init__(self, content: str = json.dumps(VALID_PAYLOAD)):
        self.content = content
        self.last_kwargs: dict = {}
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.last_kwargs = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=outer.content))],
                    usage=SimpleNamespace(prompt_tokens=1500, completion_tokens=250),
                )

        self.chat = SimpleNamespace(completions=Completions())


def test_openai_summariser_builds_the_expected_request():
    stub = StubOpenAI()
    summariser = OpenAISummariser(api_key="k", model="gpt-4o-mini", client=stub)
    note = summariser.summarise(make_article(), "cardiology")

    kwargs = stub.last_kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["temperature"] == DEFAULT_TEMPERATURE
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"] is SUMMARY_SCHEMA
    assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]

    assert note.study_type == "RCT"
    assert note.model_name == "gpt-4o-mini"
    assert note.prompt_version == PROMPT_VERSION
    assert (note.input_tokens, note.output_tokens) == (1500, 250)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o-mini", True),
        ("gpt-4o", True),
        ("o1", False),
        ("o3-mini", False),
        ("gpt-5", False),
    ],
)
def test_temperature_is_gated_on_model_capability(model, expected):
    """The legacy script sent temperature=0.2 unconditionally, which 400s on a
    reasoning model the moment OPENAI_MODEL is repointed."""
    assert supports_temperature(model) is expected

    stub = StubOpenAI()
    OpenAISummariser(api_key="k", model=model, client=stub).summarise(make_article(), "cardiology")
    assert ("temperature" in stub.last_kwargs) is expected


def test_empty_response_raises():
    summariser = OpenAISummariser(api_key="k", client=StubOpenAI(content=""))
    with pytest.raises(SummariserError, match="empty response"):
        summariser.summarise(make_article(), "cardiology")


def test_non_json_response_raises():
    summariser = OpenAISummariser(api_key="k", client=StubOpenAI(content="not json"))
    with pytest.raises(SummariserError, match="non-JSON"):
        summariser.summarise(make_article(), "cardiology")


def test_provider_exception_is_wrapped():
    class Exploding(StubOpenAI):
        def __init__(self):
            super().__init__()

            class Completions:
                def create(self, **kwargs):
                    raise RuntimeError("upstream on fire")

            self.chat = SimpleNamespace(completions=Completions())

    summariser = OpenAISummariser(api_key="k", client=Exploding())
    with pytest.raises(SummariserError, match="OpenAI call failed"):
        summariser.summarise(make_article(), "cardiology")


def test_fake_summariser_is_deterministic_and_records_calls():
    fake = FakeSummariser()
    note = fake.summarise(make_article(pmid="42"), "cardiology")
    assert fake.calls == ["42"]
    assert note.prompt_version == PROMPT_VERSION
    assert fake.summarise(make_article(pmid="42"), "cardiology") == note


def test_fake_summariser_can_simulate_failure():
    fake = FakeSummariser(fail_pmids=frozenset({"99"}))
    with pytest.raises(SummariserError):
        fake.summarise(make_article(pmid="99"), "cardiology")


# ---------------------------------------------------------------- ranking


def test_ranking_order_is_priority_then_other_priority_then_standard():
    rct = make_article(
        pmid="rct", publication_types=("Randomized Controlled Trial",), category="priority"
    )
    review = make_article(pmid="review", publication_types=("Review",), category="priority")
    standard = make_article(pmid="std", publication_types=("Journal Article",), category="standard")

    ranked = rank_summary_candidates([standard, review, rct])
    assert [a.pmid for a in ranked] == ["rct", "review", "std"]


def test_ranking_drops_short_abstracts():
    short = make_article(pmid="short", abstract="too brief")
    long = make_article(pmid="long", abstract="x" * 500)
    assert [a.pmid for a in rank_summary_candidates([short, long])] == ["long"]


def test_ranking_drops_excluded_and_low_priority_categories():
    excluded = make_article(pmid="ed", category="excluded", publication_types=("Editorial",))
    low = make_article(pmid="low", category="low_priority")
    keep = make_article(pmid="keep", category="standard")
    assert [a.pmid for a in rank_summary_candidates([excluded, low, keep])] == ["keep"]


def test_ranking_has_no_cap():
    """The legacy top-10 was an email-size limit. A feed has no such limit, and
    carrying it over would silently cap the pool at ten papers a night."""
    articles = [make_article(pmid=str(i), category="standard") for i in range(50)]
    assert len(rank_summary_candidates(articles)) == 50


def test_ranking_a_real_record(pubmed_xml):
    article = parse_efetch_response(pubmed_xml("rct"))[0]
    assert rank_summary_candidates([article]) == [article]
