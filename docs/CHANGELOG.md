# Changelog

All notable work in this repository, reconstructed from the commit history. Dates are not recorded in the available history, so entries are grouped in chronological commit order and identified by short commit ID.

## Current main

### `3187441` — Collapse the page heading into the top bar on scroll

- The sticky bar crossfades from the MedPally wordmark to the current page heading once the heading scrolls beneath it.
- The heading is read from the page DOM, avoiding duplicated title data.
- Hidden labels are removed from keyboard navigation.

### `866976c` — White Threads-style theme, top bar, and drawer

- Replaced the beige card layout with a white, divider-based feed.
- Added a sticky top bar, CSS drawer, keyboard close behaviour, and improved account-page presentation.
- Styled the Google provider button and bypassed allauth's confirmation page.

### `46688b8` — MedPally rebrand and account/search work

- Renamed the product from MedFeed to MedPally across configuration and UI.
- Added account deletion, paper search, and shared CSS.

### `ebde4cf` — More accurate RCT detection

- Recognises additional trial-title wording while avoiding false positives from reviews or abstracts that merely discuss others' trials.
- Added `reclassify_papers` to apply rule corrections to stored records without republishing old content.

### `13c746e` — Legible allauth pages

- Added an allauth base-template override so authentication pages inherit application styles.

## Delivery and operations

### `876aa8c` — Signup fix, Render blueprint, and schedulers

- Fixed allauth signup for the email-only custom user model.
- Added the Render web/cron blueprint and production hardening.
- Added primary nightly ingestion, a catch-up scheduler, and an independent ingestion freshness alarm.

### `a51ff83` — Landing page, legacy import, and operational UI

- Added the landing page, legacy newsletter CSV importer, account/catalog admin registration, vendored htmx, and error templates.

## Feed and user experience

### `1b1293e` — Feed hardening

- Made malformed cursors safe, preserved access to public paper pages during onboarding, secured paper actions, and fixed “new since last visit” display.
- Recorded paper opens, paginated Read Later, and removed redundant journal resolution work during ingestion.

### `5a0e99c` — Personalised feed with HTMX

- Built the shared-pool personalised feed, keyset pagination, seen/save/like/dismiss state, and public summary-only paper page.

### `6a3eca` — Onboarding, settings, and Google identity

- Added the three-step profile/journal/frequency onboarding flow, reusable settings pages, and Google-profile name prefill.

## Content system

### `cad522c` — Unresolved-journal administration

- Added the admin workflow that maps an unresolved paper to a canonical journal, writes its alias, repairs matching papers, and reruns relevance linking.

### `8efa244` — Database ingestion adapter

- Connected the pure engine to Django models with idempotent paper upserts, alias-based journal resolution, specialty linking, summary work, advisory locking, and run bookkeeping.

### `374c0d8` — Catalog, papers, and feed data model

- Introduced journal-first cataloguing, specialty presets, stable journal identity/aliases, the shared paper pool, summary records, and relevance links.
- Added YAML seeding and resolution of PubMed/NLM identifiers.

### `a693454` — Initial scaffold and extracted engine

- Created the Django 5.2/Python 3.13 project, environment-specific settings, custom email user, local PostgreSQL setup, CI, and framework-free engine.
- Preserved legacy parser/classifier/prompt behaviour while correcting the PubMed date window, moving specialty matching to ingest time, and removing the former top-ten digest limitation.

