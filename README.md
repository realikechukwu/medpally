# MedFeed

A per-user feed of summarised medical literature. Clinicians create an account,
pick their journals, and get a scrollable feed of papers with a short editorial
note on each — save, like, read later.

Cardiology first, but a specialty is data (a preset that preselects journals),
not code.

## Relationship to `cardiology-feed`

[`cardiology-feed`](../cardiology-feed) is the existing weekly newsletter. It is
**frozen and untouched** — it keeps sending its Friday digest from its own copy
of the pipeline, to its own Google Sheet subscriber list.

This repo owns the refactored engine. The duplication is temporary and
deliberate: it means the running newsletter carries zero risk while this is
built. Convergence comes later, by pointing the newsletter at this database or
retiring it.

## Layout

```
engine/     Pure Python. No Django imports. Fetch, parse, classify, match, summarise.
apps/       Django apps: accounts, catalog, papers, feed, ingestion.
config/     Django project and per-environment settings.
tests/      pytest. tests/engine/ needs no database and no network.
```

`engine/` is deliberately Django-free: its tests run in milliseconds, it can be
driven from a CLI before any model exists, and the boundary forces every place
where "journal" stopped being a string and became a database row to be explicit.

## Getting started

```bash
uv sync
cp .env.example .env      # then fill in NCBI_EMAIL at minimum
docker compose up -d      # local Postgres on port 5433
uv run python manage.py migrate
uv run python manage.py runserver
```

Tests, lint and types:

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy engine/
```

## Driving the engine on its own

No database, no web server:

```bash
uv run python -m engine.cli fetch --journal Circulation --since 14 | jq '.[0]'
uv run python -m engine.cli fetch --journal Circulation --since 14 -o out.json
uv run python -m engine.cli summarise out.json --specialty cardiology --limit 3 --fake
```

Drop `--fake` to use the real model (needs `OPENAI_API_KEY`; costs a fraction of
a cent per paper on `gpt-4o-mini`).

The general-journal filter, which is what keeps a cardiologist's NEJM feed to
cardiology:

```bash
uv run python -m engine.cli fetch \
  --journal "The New England journal of medicine" --since 21 \
  --specialty-mesh "Heart Failure" --specialty-keyword "cardi*"
```

## Things worth knowing

**The ingest window uses `[edat]`, not `[dp]`.** `cardiology-feed` windows on
publication date. For journals that publish online-ahead-of-print, or that
forward-date an issue, that can be months away from the day PubMed indexed the
record — so papers are silently missed. One captured fixture (`rct.xml`) has a
publication date of 2025-11-08 and an Entrez date of 2026-02-09. Publication date
is still parsed and shown, but only for display; ordering and the ingest window
use the Entrez date.

**Most fresh papers have no MeSH terms.** MeSH headings arrive weeks to months
after publication. In a live 3-week NEJM window, only 10 of 74 records carried
any. That is why relevance matching reads title and abstract as well, why
vocabulary terms support a `cardi*` stem wildcard, and why `recheck_relevance`
re-runs matching over recent papers as their MeSH lands.

**Journals are matched on NLM ID and ISSN, not title.** PubMed returns
"European heart journal", "JAMA cardiology", "BMJ open" and "Lancet (London,
England)" — none of which match the strings in `cardiology-feed`'s specialty
configs. Every record carries a stable `NlmUniqueID`.

**The summarisation prompt is copied verbatim** from `cardiology-feed` and is
versioned (`PROMPT_VERSION`). It is the product voice; changing it means bumping
the version so stored summaries can be selectively regenerated.
