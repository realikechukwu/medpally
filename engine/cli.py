"""Standalone CLI for the engine — no Django, no database, no web server.

This exists so the engine can be exercised and judged on its own before any
model or view is written:

    python -m engine.cli fetch --journal Circulation --since 7 | jq
    python -m engine.cli fetch --journal Circulation --since 7 -o out.json
    python -m engine.cli summarise out.json --specialty cardiology --limit 3
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .pubmed.client import PubMedClient
from .pubmed.models import FetchedArticle
from .relevance import SpecialtyRules, match_specialty
from .summarise.client import FakeSummariser, OpenAISummariser
from .summarise.select import rank_summary_candidates


def _as_dict(article: FetchedArticle) -> dict[str, Any]:
    data = dataclasses.asdict(article)
    data["journal"] = dict(data["journal"])
    data["pub_date"] = article.pub_date.isoformat() if article.pub_date else None
    data["entrez_date"] = article.entrez_date.isoformat() if article.entrez_date else None
    data["url"] = article.url
    return data


def _from_dict(data: dict[str, Any]) -> FetchedArticle:
    from .pubmed.models import JournalIdentity

    def _date(value: str | None) -> date | None:
        return date.fromisoformat(value) if value else None

    return FetchedArticle(
        pmid=data["pmid"],
        title=data["title"],
        abstract=data["abstract"],
        journal=JournalIdentity(**data["journal"]),
        pub_date_raw=data["pub_date_raw"],
        pub_date=_date(data.get("pub_date")),
        entrez_date=_date(data.get("entrez_date")),
        doi=data.get("doi", ""),
        publication_types=tuple(data.get("publication_types", ())),
        mesh_terms=tuple(data.get("mesh_terms", ())),
        authors=tuple(data.get("authors", ())),
        category=data.get("category", ""),
        keywords=tuple(data.get("keywords", ())),
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    email = os.getenv("NCBI_EMAIL", "")
    if not email:
        print("NCBI_EMAIL must be set (E-utilities requires a contact address).", file=sys.stderr)
        return 2

    client = PubMedClient(
        email=email,
        api_key=os.getenv("NCBI_API_KEY", ""),
        tool=os.getenv("PUBMED_TOOL_NAME", "medpally"),
    )

    date_to = date.fromisoformat(args.to) if args.to else datetime.now(UTC).date()
    date_from = (
        date.fromisoformat(args.since_date)
        if args.since_date
        else date_to - timedelta(days=args.since)
    )

    articles = list(client.fetch_journal_window(args.journal, date_from, date_to))

    if args.specialty_mesh or args.specialty_keyword:
        rules = SpecialtyRules(
            slug="cli",
            mesh_terms=tuple(args.specialty_mesh),
            title_keywords=tuple(args.specialty_keyword),
        )
        articles = [
            a
            for a in articles
            if match_specialty(
                rules=rules, mesh_terms=a.mesh_terms, title=a.title, abstract=a.abstract
            ).matched
        ]

    payload = [_as_dict(a) for a in articles]
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(
            f"{len(payload)} articles ({date_from}..{date_to}) -> {args.output}",
            file=sys.stderr,
        )
    else:
        print(text)
    return 0


def cmd_summarise(args: argparse.Namespace) -> int:
    with open(args.input, encoding="utf-8") as fh:
        articles = [_from_dict(d) for d in json.load(fh)]

    ranked = rank_summary_candidates(articles)[: args.limit]

    if args.fake:
        summariser: Any = FakeSummariser()
    else:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            print("OPENAI_API_KEY must be set (or pass --fake).", file=sys.stderr)
            return 2
        summariser = OpenAISummariser(
            api_key=api_key, model=os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        )

    for article in ranked:
        note = summariser.summarise(article, args.specialty)
        print(f"\n\033[1m{article.title}\033[0m")
        print(f"  {article.journal.best_name} · {article.pub_date_raw} · pmid {article.pmid}")
        print(f"  STUDY TYPE  {note.study_type}")
        print(f"  CONTEXT     {note.context}")
        print(f"  FINDING     {note.finding}")
        print(f"  SO WHAT     {note.so_what}")
        print(f"  TAGS        {', '.join(note.tags)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engine.cli", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch articles from PubMed")
    fetch.add_argument("--journal", action="append", required=True, help="repeatable")
    fetch.add_argument("--since", type=int, default=7, help="days back from --to (default 7)")
    fetch.add_argument("--since-date", help="explicit start date, YYYY-MM-DD")
    fetch.add_argument("--to", help="explicit end date, YYYY-MM-DD (default today, UTC)")
    fetch.add_argument("--specialty-mesh", action="append", default=[])
    fetch.add_argument("--specialty-keyword", action="append", default=[])
    fetch.add_argument("-o", "--output", help="write JSON here instead of stdout")
    fetch.set_defaults(func=cmd_fetch)

    summarise = sub.add_parser("summarise", help="summarise a fetch output file")
    summarise.add_argument("input")
    summarise.add_argument("--specialty", default="cardiology")
    summarise.add_argument("--limit", type=int, default=5)
    summarise.add_argument("--fake", action="store_true", help="use the stub summariser")
    summarise.set_defaults(func=cmd_summarise)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
