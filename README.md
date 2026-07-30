# MedPally

MedPally is a personalised, web-based feed of summarised medical literature. Clinicians choose a specialty and journals; a scheduled pipeline imports PubMed records, identifies relevant papers, and produces a short editorial note for each eligible paper. Users can search, save, like, dismiss, and share papers.

The application is cardiology-first, but specialties are catalogue data rather than application code. The existing `cardiology-feed` newsletter is a separate, frozen system; MedPally does not send a digest email.

## Start here

- [Local development and reconstruction guide](docs/DEVELOPMENT.md)
- [Architecture and data model](docs/ARCHITECTURE.md)
- [Ingestion, catalogue, and maintenance runbook](docs/OPERATIONS.md)
- [Deployment guide](docs/DEPLOY.md)
- [History-derived changelog](docs/CHANGELOG.md)

## Quick start

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), Docker, and Docker Compose.

```bash
uv sync
cp .env.example .env
docker compose up -d
uv run python manage.py migrate
uv run python manage.py seed_catalog
uv run python manage.py runserver
```

Set `NCBI_EMAIL` in `.env` before running ingestion. `OPENAI_API_KEY` is only needed for real summaries; use `--fake-summariser` or `summarise_papers --fake` when developing without it.

## Quality checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy engine/
```

## Repository map

```text
apps/       Django product apps and management commands
config/     Django settings and URL/WSGI configuration
engine/     Framework-independent PubMed, relevance, classification, and AI logic
templates/  Server-rendered Django templates
static/     CSS, JavaScript, and vendored htmx
tests/      Unit, integration, and route-level tests
docs/       Maintainer documentation and changelog
```
