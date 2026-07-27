from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def pubmed_xml():
    """Load a captured efetch response by name from tests/fixtures/pubmed/."""

    def _load(name: str) -> bytes:
        return (FIXTURES / "pubmed" / f"{name}.xml").read_bytes()

    return _load
