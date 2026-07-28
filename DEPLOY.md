# Deploying MedPally

Everything that can be built and verified without credentials already is. What
follows is the part that needs accounts and secrets.

Order matters: Supabase gives you `DATABASE_URL`, which both GitHub and Render
need.

---

## 1. Supabase — the database

1. Create a project. Choose a region near your users (`eu-west-*` for the UK).
2. Connect → **Session pooler** → copy the URI. It looks like:

   ```
   postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

   **Use port 5432 (session), not 6543 (transaction).** The transaction pooler
   breaks server-side cursors and prepared statements, and migrations through
   it hang partway and leave the schema inconsistent.

3. Do not enable RLS on Django-owned tables — the app connects as a privileged
   role and never goes through PostgREST. Do not create tables in the Supabase
   SQL editor: Django owns the schema, and a hand-made table will collide with
   a migration later.

The free tier pauses a project after 7 days idle. The nightly ingestion keeps
it warm, so this only bites if both schedulers are off.

---

## 2. GitHub — the repository and the primary nightly job

```bash
gh repo create medpally --private --source=. --remote=origin --push
```

Then set repository secrets (Settings → Secrets and variables → Actions):

| Secret | Where it comes from |
|---|---|
| `DATABASE_URL` | Supabase session pooler URI, step 1 |
| `DJANGO_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `NCBI_EMAIL` | your contact address — E-utilities requires one |
| `NCBI_API_KEY` | ncbi.nlm.nih.gov account → Settings → API Key Management |
| `OPENAI_API_KEY` | **issue a fresh key for this app** so it can be revoked independently of the old newsletter |
| `SENTRY_DSN` | optional |

Two workflows start running once these exist:

- `.github/workflows/nightly.yml` — 02:00 UTC, the primary ingestion run.
- `.github/workflows/freshness-alarm.yml` — 12:00 UTC, fails (and emails you)
  if no ingestion has succeeded in 36 hours. Deliberately separate, because a
  check that only runs inside the nightly job cannot detect the nightly job
  never starting.

Trigger the first run by hand from the Actions tab rather than waiting for
02:00 — it is the fastest way to find a wrong secret.

---

## 3. Render — the web app

Dashboard → **New → Blueprint** → pick the repo. `render.yaml` declares both
services; Render will prompt for every secret marked `sync: false`.

Paste the same values as above, plus:

| Variable | Notes |
|---|---|
| `SITE_BASE_URL` | `https://<service>.onrender.com` — used in share links |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` | step 4 |
| `BREVO_SMTP_USER` / `BREVO_SMTP_KEY` | step 5 |
| `DEFAULT_FROM_EMAIL` | e.g. `MedPally <noreply@medpally.com>` |

`DJANGO_ALLOWED_HOSTS` is **not** in the list: production settings pick up
Render's `RENDER_EXTERNAL_HOSTNAME` automatically, so the first deploy works
before you have hand-copied the URL anywhere. Set it explicitly only when you
add a custom domain.

Notes on the shape of this blueprint:

- `migrate` runs in `buildCommand`, not `preDeployCommand`, because pre-deploy
  needs a paid instance. Safe here **only because a free service runs a single
  instance** — if you scale beyond one, move it to `preDeployCommand` or two
  replicas will race the same migration.
- The free web service sleeps after ~15 min idle and takes ~1 min to wake. The
  first visitor after a quiet night waits.
- The `medpally-nightly` cron is `plan: starter` (~$1/mo) because **Render does
  not run cron jobs on free instances.** If you would rather not pay it yet,
  delete that service from `render.yaml` — GitHub Actions is the primary
  scheduler and the app is fully functional without the second one. You lose
  only the catch-up run.

### After the first successful deploy

```bash
python manage.py seed_catalog
python manage.py resolve_journals
```

Run these once against Supabase (Render shell, or locally with `DATABASE_URL`
pointed at Supabase). `resolve_journals` calls the NLM Catalog and fills in
`nlm_uid` and ISSNs — this is what stops the same journal appearing twice under
two spellings. Then trigger `nightly.yml` manually to populate the feed.

---

## 4. Google OAuth

Google Cloud Console → APIs & Services → Credentials → OAuth client ID → Web
application.

- Authorised JavaScript origin: `https://<service>.onrender.com`
- Authorised redirect URI:
  `https://<service>.onrender.com/accounts/google/login/callback/`

Email/password signup works without this; only the Google button needs it.

---

## 5. Brevo SMTP — auth email only

Password reset is the only thing that sends mail. Without it, a locked-out user
stays locked out.

Set `BREVO_SMTP_USER` and `BREVO_SMTP_KEY`, and set
`EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend` (production
settings already default to SMTP).

**The digest newsletter still comes from the old `cardiology-feed` repo.** This
app sends no digest. Anyone on both lists is on two systems until cutover —
the onboarding copy says so.

---

## 6. Legacy subscribers (optional)

Export the newsletter Google Sheet to CSV, then:

```bash
python manage.py import_legacy_subscribers subscribers.csv --dry-run
```

Check the counts, then run it without `--dry-run`. This only *prefills*
onboarding for someone who signs up with a matching address — it never creates
accounts, and it never touches the old repo's sending list.

---

## Verifying the deployment

1. `https://<service>.onrender.com/healthz` → `{"status": "ok", ...}` with an
   `ingestion.stale` of `false` once the first run has completed.
2. Sign up in an incognito window → the wizard → a populated feed.
3. Open a paper's `/p/<pmid>/` link while signed out — it should render.
4. Two consecutive unattended nightly runs, with the feed growing on its own
   and no `IngestionRun` rows stuck in `running`.

## When something is wrong

| Symptom | Cause |
|---|---|
| Every request 400s, `DisallowedHost` | `RENDER_EXTERNAL_HOSTNAME` missing and `DJANGO_ALLOWED_HOSTS` unset |
| 500 on every DB page, `server does not support SSL` | `DATABASE_URL` points at something without TLS |
| Migrations hang forever | You used the transaction pooler (6543) instead of the session pooler (5432) |
| Feed is empty for everyone | `seed_catalog` / `resolve_journals` never ran |
| Feed stopped growing | Check the freshness alarm workflow, then `IngestionRun` in the admin for stale `running` rows |
| CSS/JS 404 in production | `collectstatic` failed during build; manifest storage 500s on any unhashed reference |
