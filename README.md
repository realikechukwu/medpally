# MedPally

MedPally is a personalised, web-based feed of summarised medical literature. Clinicians choose a specialty and journals; a scheduled pipeline imports PubMed records, identifies relevant papers, and produces a short editorial note for each eligible paper. Users can search, save, like, dismiss, and share papers.

The application is cardiology-first, but specialties are catalogue data rather than application code. The existing `cardiology-feed` newsletter is a separate, frozen system; MedPally does not send a digest email.

> **This is not open source.** The source is public for reference and transparency only. All rights are reserved — you may read it, but you may not use, copy, modify, or redistribute it, for commercial or non-commercial purposes, without prior written permission. See [LICENSE](LICENSE).

## Repository map

```text
apps/       Django product apps and management commands
config/     Django settings and URL/WSGI configuration
engine/     Framework-independent PubMed, relevance, classification, and AI logic
templates/  Server-rendered Django templates
static/     CSS, JavaScript, vendored htmx, and the brand artwork
tests/      Unit, integration, and route-level tests
tools/      Developer scripts that are not part of the running application
```

The app icons, favicons, and Open Graph card in `static/img/brand/` are
generated: run `python tools/brand_assets.py` after changing the monogram or
the brand colour, and commit the result.

Oracle Always Free deployment and Render cutover instructions are in
[setuporacle.md](setuporacle.md).

## Before pushing

Run `make ci` to execute the same checks as GitHub Actions. To prevent a
formatting-only CI failure at commit time, enable the repository hook once:

```bash
git config core.hooksPath .githooks
```

## License

Proprietary — copyright © 2026 Ikechukwu Chukwudi, all rights reserved. Public for reference only, not for reuse. See [LICENSE](LICENSE) for the full terms and for how to request permission.
