"""Summariser implementations behind one protocol.

The protocol is what makes everything downstream testable with no network and no
API key: FakeSummariser is a drop-in for the ingestion pipeline's tests.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, cast

from ..errors import SummariserError
from ..pubmed.models import FetchedArticle
from .prompt import PROMPT_VERSION, build_system_prompt, build_user_prompt
from .schema import SUMMARY_SCHEMA, EditorialNote

logger = logging.getLogger(__name__)

# Models that reject the `temperature` parameter outright. The legacy script sent
# temperature=0.2 unconditionally, which 400s the moment OPENAI_MODEL is pointed
# at a reasoning model.
NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5")

DEFAULT_TEMPERATURE = 0.2


def supports_temperature(model: str) -> bool:
    return not model.lower().startswith(NO_TEMPERATURE_PREFIXES)


class Summariser(Protocol):
    """Turns one article into one editorial note."""

    def summarise(self, article: FetchedArticle, specialty_name: str) -> EditorialNote: ...


class OpenAISummariser:
    def __init__(self, *, api_key: str, model: str = "gpt-4o-mini", client: Any = None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
        self.client = client
        self.model = model

    def summarise(self, article: FetchedArticle, specialty_name: str) -> EditorialNote:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_system_prompt(specialty_name)},
                {
                    "role": "user",
                    "content": build_user_prompt(
                        title=article.title,
                        journal=article.journal.best_name,
                        pub_date=article.pub_date_raw,
                        publication_types=article.publication_types,
                        abstract=article.abstract,
                    ),
                },
            ],
            "response_format": cast(Any, {"type": "json_schema", "json_schema": SUMMARY_SCHEMA}),
        }
        if supports_temperature(self.model):
            kwargs["temperature"] = DEFAULT_TEMPERATURE

        try:
            completion = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise SummariserError(f"OpenAI call failed for pmid {article.pmid}: {exc}") from exc

        content = completion.choices[0].message.content
        if not content:
            raise SummariserError(f"OpenAI returned an empty response for pmid {article.pmid}")

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SummariserError(f"OpenAI returned non-JSON for pmid {article.pmid}") from exc

        usage = getattr(completion, "usage", None)
        return EditorialNote.from_payload(
            payload,
            model_name=self.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


class FakeSummariser:
    """Deterministic stand-in for tests and dry runs. Never touches the network."""

    def __init__(self, *, model: str = "fake-model", fail_pmids: frozenset[str] = frozenset()):
        self.model = model
        self.fail_pmids = fail_pmids
        self.calls: list[str] = []

    def summarise(self, article: FetchedArticle, specialty_name: str) -> EditorialNote:
        self.calls.append(article.pmid)
        if article.pmid in self.fail_pmids:
            raise SummariserError(f"simulated failure for pmid {article.pmid}")
        return EditorialNote(
            study_type="Other",
            context=f"Fake context for {article.pmid}.",
            finding=f"Fake finding for {article.title[:40]}.",
            so_what=f"Fake implication for a {specialty_name} audience.",
            tags=("Fake Tag",),
            model_name=self.model,
            prompt_version=PROMPT_VERSION,
        )
