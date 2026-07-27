import pytest
from django.core.management import call_command
from django.db import IntegrityError

from apps.catalog.models import Journal, JournalAlias, Specialty, SpecialtyJournal

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------- aliases


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("The Lancet", "Lancet"),
        ("The BMJ", "BMJ"),
        ("JACC: Cardiovascular Imaging", "JACC Cardiovascular Imaging"),
        ("The New England journal of medicine", "New England Journal of Medicine"),
        ("European Heart Journal", "European heart journal"),
    ],
)
def test_alias_normalisation_collapses_real_config_variants(a, b):
    """These pairs appear as separate entries across cardiology-feed's configs."""
    assert JournalAlias.normalize(a) == JournalAlias.normalize(b)


def test_alias_normalisation_keeps_distinct_journals_apart():
    assert JournalAlias.normalize("Heart") != JournalAlias.normalize("Heart Rhythm")
    assert JournalAlias.normalize("Circulation") != JournalAlias.normalize(
        "Circulation: Heart Failure"
    )


def test_alias_value_normalized_is_written_on_save():
    journal = Journal.objects.create(
        slug="j", pubmed_name="Lancet", display_name="The Lancet", short_name="Lancet"
    )
    alias = JournalAlias.objects.create(journal=journal, value="The Lancet")
    assert alias.value_normalized == "lancet"


def test_two_journals_cannot_claim_the_same_alias():
    a = Journal.objects.create(slug="a", pubmed_name="A", display_name="A", short_name="A")
    b = Journal.objects.create(slug="b", pubmed_name="B", display_name="B", short_name="B")
    JournalAlias.objects.create(journal=a, value="The Lancet")
    with pytest.raises(IntegrityError):
        JournalAlias.objects.create(journal=b, value="Lancet")


def test_identifiers_are_normalised_verbatim_not_as_titles():
    """An ISSN's punctuation is meaningful; a title's is not."""
    assert JournalAlias.normalize("1355-6037", JournalAlias.Kind.ISSN) == "1355-6037"
    assert JournalAlias.normalize("The Heart") == "heart"


# ---------------------------------------------------------------- constraints


def test_nlm_uid_is_unique_when_present():
    Journal.objects.create(
        slug="a", pubmed_name="A", display_name="A", short_name="A", nlm_uid="0147763"
    )
    with pytest.raises(IntegrityError):
        Journal.objects.create(
            slug="b", pubmed_name="B", display_name="B", short_name="B", nlm_uid="0147763"
        )


def test_many_journals_may_share_an_empty_nlm_uid():
    """The uniqueness constraint is conditional so unresolved journals coexist."""
    for i in range(3):
        Journal.objects.create(
            slug=f"j{i}", pubmed_name=f"J{i}", display_name=f"J{i}", short_name=f"J{i}"
        )
    assert Journal.objects.filter(nlm_uid="").count() == 3


def test_is_resolved_reflects_having_any_stable_identifier():
    journal = Journal.objects.create(slug="a", pubmed_name="A", display_name="A", short_name="A")
    assert not journal.is_resolved
    journal.issn_electronic = "1524-4539"
    assert journal.is_resolved


def test_specialty_to_rules_adapts_to_the_engine_dataclass():
    specialty = Specialty.objects.create(
        slug="cardiology",
        name="Cardiology",
        mesh_terms=["Heart Failure"],
        title_keywords=["cardi*"],
        abstract_keywords=["ejection fraction"],
    )
    rules = specialty.to_rules()
    assert rules.slug == "cardiology"
    assert rules.mesh_terms == ("Heart Failure",)
    assert rules.title_keywords == ("cardi*",)


# ---------------------------------------------------------------- seed_catalog

SEED_YAML = """
specialties:
  - slug: cardiology
    name: Cardiology
    mesh_terms: ["Heart Failure"]
    title_keywords: ["cardi*"]
journals:
  - slug: circulation
    pubmed_name: Circulation
    display_name: Circulation
    short_name: Circ
    is_general: false
    cover_color: "#9f1239"
    specialties: ["cardiology"]
  - slug: lancet
    pubmed_name: Lancet
    display_name: The Lancet
    short_name: Lancet
    is_general: true
    cover_color: "#334155"
    aliases: ["The Lancet"]
    specialties: ["cardiology"]
"""


@pytest.fixture
def seed_file(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(SEED_YAML)
    return str(path)


def test_seed_catalog_creates_everything(seed_file):
    call_command("seed_catalog", file=seed_file)
    assert Specialty.objects.count() == 1
    assert Journal.objects.count() == 2
    assert SpecialtyJournal.objects.count() == 2
    assert Journal.objects.get(slug="lancet").is_general


def test_seed_catalog_is_idempotent(seed_file):
    call_command("seed_catalog", file=seed_file)
    before = (
        Journal.objects.count(),
        JournalAlias.objects.count(),
        SpecialtyJournal.objects.count(),
    )
    call_command("seed_catalog", file=seed_file)
    after = (
        Journal.objects.count(),
        JournalAlias.objects.count(),
        SpecialtyJournal.objects.count(),
    )
    assert before == after


def test_seed_catalog_does_not_duplicate_aliases_that_normalise_together(seed_file):
    """ "Lancet" and "The Lancet" are one alias, not two."""
    call_command("seed_catalog", file=seed_file)
    lancet = Journal.objects.get(slug="lancet")
    assert lancet.aliases.count() == 1


def test_seed_catalog_dry_run_changes_nothing(seed_file):
    call_command("seed_catalog", file=seed_file, dry_run=True)
    assert Journal.objects.count() == 0
    assert Specialty.objects.count() == 0


def test_seed_catalog_updates_in_place(seed_file, tmp_path):
    call_command("seed_catalog", file=seed_file)
    edited = tmp_path / "edited.yaml"
    edited.write_text(SEED_YAML.replace("short_name: Circ", "short_name: CIRC"))
    call_command("seed_catalog", file=edited)
    assert Journal.objects.get(slug="circulation").short_name == "CIRC"
    assert Journal.objects.count() == 2


# ---------------------------------------------------------------- covers


def test_journal_cover_renders_the_abbreviation_and_colour():
    from apps.catalog.templatetags.covers import journal_cover

    journal = Journal.objects.create(
        slug="nejm",
        pubmed_name="N Engl J Med",
        display_name="NEJM",
        short_name="NEJM",
        cover_color="#1e3a5f",
    )
    svg = journal_cover(journal)
    assert "<svg" in svg
    assert "NEJM" in svg
    assert "#1e3a5f" in svg
    # No external references — the CSP on a published page would block them.
    assert "http://" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")


def test_journal_cover_handles_a_missing_journal():
    from apps.catalog.templatetags.covers import journal_cover

    assert "?" in journal_cover(None)


def test_journal_cover_escapes_hostile_input():
    from apps.catalog.templatetags.covers import journal_cover

    # Unsaved: short_name is capped at 24 chars in the schema, and the escaping
    # is a pure rendering concern anyway.
    journal = Journal(slug="x", short_name="<script>x</script>", cover_color="#000000")
    svg = journal_cover(journal)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_journal_cover_escapes_a_hostile_colour():
    from apps.catalog.templatetags.covers import journal_cover

    journal = Journal(slug="x", short_name="OK", cover_color='"/><script>alert(1)</script>')
    svg = journal_cover(journal)
    assert "<script>" not in svg


def test_long_abbreviations_shrink_to_fit():
    from apps.catalog.templatetags.covers import journal_cover

    short = Journal(slug="a", short_name="EHJ", cover_color="#000000")
    long = Journal(slug="b", short_name="Circ Arrhythm EP", cover_color="#000000")
    assert 'font-size="30"' in journal_cover(short)
    assert 'font-size="30"' not in journal_cover(long)
