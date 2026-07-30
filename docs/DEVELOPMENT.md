# Development and reconstruction guide

This guide is the shortest reliable path for recreating a working MedPally installation from a clean checkout.

## Prerequisites

- Python 3.13 (the supported range is `>=3.13,<3.14`)
- `uv`
- Docker and Docker Compose, for the local PostgreSQL service
- An NCBI email address for real PubMed requests
- An OpenAI API key only when generating real summaries

## Create a local environment

```bash
uv sync
cp .env.example .env
docker compose up -d
uv run python manage.py migrate
uv run python manage.py seed_catalog
uv run python manage.py runserver
```

The app runs at `http://localhost:8000`. Local PostgreSQL listens on host port `5433`, deliberately avoiding a common local PostgreSQL installation on `5432`. The default database is `medfeed`; tests use `medfeed_test`.

Use `uv run python manage.py createsuperuser` to access `/admin/`.

### Required local configuration

Copy `.env.example` exactly and adjust only the values needed locally.

| Variable | Why it matters |
|---|---|
| `NCBI_EMAIL` | Required contact value for PubMed E-utilities. |
| `NCBI_API_KEY` | Optional; increases the PubMed rate limit. |
| `OPENAI_API_KEY` | Needed only for a real AI summary request. |
| `OPENAI_MODEL` | Defaults to `gpt-4o-mini`. |
| `DATABASE_URL` | Defaults to the Compose database. |
| `DJANGO_SECRET_KEY` | Use a non-default value outside local development. |

Never commit `.env`. Production configuration and every deployment secret are described in [DEPLOY.md](DEPLOY.md).

## Initialise the catalogue and ingest content

`seed_catalog` is idempotent. It converts `apps/catalog/fixtures/catalog.yaml` into specialties, journals, aliases, and specialty presets.

```bash
uv run python manage.py seed_catalog
uv run python manage.py resolve_journals
uv run python manage.py ingest_papers --since-days 3 --fake-summariser
```

`resolve_journals` makes live PubMed calls and should be run after the initial seed (and after introducing a journal). It enriches each journal with stable NLM/ISSN identifiers and aliases. Do not skip it in a real environment: an unresolved journal cannot be reliably attached to imported papers.

For a production-like ingest with real summaries, remove `--fake-summariser` and provide `OPENAI_API_KEY`. To build a backlog without AI calls, use `ingest_papers --no-summaries`, then run `summarise_papers` independently.

## Application boundaries

`engine/` must remain independent of Django. It contains plain Python data objects and logic for PubMed requests/parsing, paper classification, specialty matching, and structured AI summaries. `apps/ingestion/services.py` is the adapter between engine objects and Django models.

This separation is intentional: engine tests are fast and do not need a database or network. New product behaviour should usually be implemented in a Django app; extraction into `engine/` is appropriate only for reusable, framework-free logic.

## Common development tasks

| Task | Command |
|---|---|
| Run all tests | `uv run pytest` |
| Run engine-only tests | `uv run pytest tests/engine` |
| Check formatting/lint | `uv run ruff check . && uv run ruff format --check .` |
| Type-check the pure engine | `uv run mypy engine/` |
| Create migrations | `uv run python manage.py makemigrations` |
| Apply migrations | `uv run python manage.py migrate` |
| Inspect configuration | `uv run python manage.py diffsettings` |
| Generate static assets | `uv run python manage.py collectstatic --noinput` |

Tests use `config.settings.test`, captured PubMed XML fixtures, a fake summariser, and mocked HTTP. Tests marked `network` are excluded from ordinary CI runs.

## Stand-alone engine CLI

The engine can be exercised without Django or a database:

```bash
uv run python -m engine.cli fetch --journal Circulation --since 14 -o out.json
uv run python -m engine.cli summarise out.json --specialty cardiology --limit 3 --fake
```

Drop `--fake` only when an OpenAI key is available. The CLI is useful for validating PubMed queries and prompt output before changing product code.

## Rebuild checklist

1. Install dependencies with the locked `uv.lock` file.
2. Configure PostgreSQL and environment values.
3. Apply Django migrations.
4. Seed the catalogue.
5. Resolve seeded journals against PubMed.
6. Run an ingest, relevance recheck, and summary pass.
7. Create an admin user and verify `/healthz`, `/admin/`, signup/onboarding, and `/feed/`.
8. Configure both the primary scheduler and independent freshness alarm.

The final step matters: a healthy web server can still serve a silently stale feed.
