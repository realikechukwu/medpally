"""The editorial-note output contract.

SUMMARY_SCHEMA is copied verbatim from cardiology-feed's summarise_and_email.py.
It is a strict OpenAI json_schema, so the model cannot return extra fields or
omit required ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SUMMARY_SCHEMA: dict[str, Any] = {
    "name": "editorial_note",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "study_type": {"type": "string"},
            "context": {"type": "string"},
            "finding": {"type": "string"},
            "so_what": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 0, "maxItems": 4},
        },
        "required": ["study_type", "context", "finding", "so_what", "tags"],
    },
    "strict": True,
}


@dataclass(frozen=True, slots=True)
class EditorialNote:
    study_type: str
    context: str
    finding: str
    so_what: str
    tags: tuple[str, ...]
    model_name: str = ""
    prompt_version: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any], **meta: Any) -> EditorialNote:
        from ..classify import normalize_study_type

        return cls(
            study_type=normalize_study_type(str(payload.get("study_type", ""))),
            context=str(payload.get("context", "")),
            finding=str(payload.get("finding", "")),
            so_what=str(payload.get("so_what", "")),
            tags=tuple(str(t) for t in payload.get("tags", []) if str(t).strip()),
            **meta,
        )
