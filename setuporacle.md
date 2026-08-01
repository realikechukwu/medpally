# Move MedPally from Render to Oracle Always Free

This runbook moves the MedPally web service from Render to an always-on Oracle
Cloud Infrastructure (OCI) VM. It does **not** move the PostgreSQL database:
Render does not host that database today; Supabase does. It also leaves the
nightly ingestion and featured-paper schedules in GitHub Actions, where they
already run independently of Render.

The resulting production path is:

```text
Browser -> OCI public IP -> Caddy (HTTPS) -> Gunicorn/Django -> Supabase Postgres
                                                   |
                                                   +-> OpenAI, PubMed, Brevo, Sentry
GitHub Actions -----------------------------------------> Supabase Postgres
```

The deployment files are under `deploy/oracle/`. Caddy obtains and renews the
TLS certificate automatically. Only ports 80 and 443 are published; Django's
port and the database are never exposed on the VM.

## 1. Before creating anything

Use the OCI home region: Always Free compute and storage must be created there.
Oracle's current Always Free resource page describes an Ampere A1 allowance
equivalent to 2 OCPUs and 12 GB RAM and 200 GB of combined boot/block storage.
The OCI Console is the authority for the entitlement shown on your account.

Recommended VM:

- Shape: `VM.Standard.A1.Flex` (Arm/Aarch64), marked **Always Free eligible**
- Image: Ubuntu 24.04 LTS (Aarch64)
- Size: 2 OCPUs and 12 GB RAM, or the largest Always Free allocation the
  Console permits without showing an estimated charge
- Boot volume: 50 GB is enough for this app; leave room inside the free 200 GB
  storage allowance
- Public IPv4: assign one, then reserve it so a stop/start cannot change DNS

Do not continue if the OCI create-instance screen shows an estimated charge.
Capacity errors for A1 instances are common; Oracle recommends trying another
availability domain or waiting and retrying.

Official references:

- [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [OCI Free Tier overview](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [OCI block-volume backup policies](https://docs.oracle.com/en-us/iaas/Content/Block/Tasks/schedulingvolumebackups.htm)

## 2. Create the VM and network rules

In OCI Console:

1. Create or select a compartment for MedPally.
2. Create a VCN with a public subnet and internet gateway, or use the VCN
   wizard's public-network option.
3. Create the Ampere A1 VM described above and upload/paste your SSH public key.
4. Reserve the instance's public IPv4 address.
5. In the subnet security list or Network Security Group, allow ingress:
   - TCP 22 from **your own public IP/32**, not from the whole internet.
   - TCP 80 from `0.0.0.0/0` and `::/0` when IPv6 is configured.
   - TCP 443 from `0.0.0.0/0` and `::/0` when IPv6 is configured.
   - UDP 443 from the same sources for HTTP/3; this is optional.
6. Do **not** open ports 8000, 5432, or 6543.

Docker-published ports can bypass `ufw` rules, so the OCI Security List/NSG is
the outer firewall that must stay restrictive. This stack publishes only
Caddy's 80/443 ports.

## 3. Point a temporary hostname at Oracle

Before changing the live domain, create a temporary DNS A record such as:

```text
oracle-test.medpally.com -> OCI_RESERVED_PUBLIC_IP
```

Use a 300-second TTL during migration. Wait until this resolves publicly:

```bash
dig +short oracle-test.medpally.com
```

Caddy cannot issue the test hostname's TLS certificate until DNS points to the
VM and ports 80/443 are reachable.

## 4. Install Docker on Ubuntu

Connect using the SSH username shown by OCI (normally `ubuntu`):

```bash
ssh ubuntu@OCI_RESERVED_PUBLIC_IP
```

Install Docker from Docker's signed Ubuntu repository:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
exit
```

Reconnect so the Docker group takes effect. Membership in the Docker group is
effectively root access; only trusted administrators should receive it.

## 5. Clone the Oracle branch

```bash
sudo mkdir -p /opt/medpally
sudo chown "$USER":"$USER" /opt/medpally
git clone https://github.com/realikechukwu/medpally.git /opt/medpally
cd /opt/medpally
git switch oracle-switch
```

After this branch is merged, production should track `main` instead:

```bash
git switch main
git pull --ff-only
```

## 6. Create the production environment file

```bash
cd /opt/medpally
cp deploy/oracle/.env.example deploy/oracle/.env
chmod 600 deploy/oracle/.env
openssl rand -hex 48
nano deploy/oracle/.env
```

Paste the generated value into `DJANGO_SECRET_KEY`. Copy the remaining secret
values from the current Render environment; do not generate replacements for
the Supabase URL, OAuth credentials, Brevo key, OpenAI key, or NCBI key.

For the temporary test deployment set:

```dotenv
DOMAIN=oracle-test.medpally.com
DJANGO_ALLOWED_HOSTS=oracle-test.medpally.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://oracle-test.medpally.com
SITE_BASE_URL=https://oracle-test.medpally.com
```

Important environment details:

- `DATABASE_URL` must remain the Supabase **session pooler on port 5432**. Do
  not use the transaction pooler on 6543.
- URL-encode special characters in the database password.
- Keep `DB_CONN_MAX_AGE=0`; the free Supabase session pool has limited slots.
- `DJANGO_ALLOWED_HOSTS` is a comma-separated hostname list with no scheme.
- `DJANGO_CSRF_TRUSTED_ORIGINS` contains full HTTPS origins.
- Never commit `deploy/oracle/.env`; it is ignored by Git.

## 7. Build and start the test deployment

```bash
cd /opt/medpally
ORACLE_ENV_FILE=.env docker compose -f deploy/oracle/compose.yaml config --quiet
deploy/oracle/update.sh
```

The first build downloads Arm-compatible Python, uv, and Caddy images. The web
container then collects static files, applies Django migrations, and starts
Gunicorn. Caddy waits for the web health check before proxying traffic.

Inspect the deployment:

```bash
docker compose -f deploy/oracle/compose.yaml ps
docker compose -f deploy/oracle/compose.yaml logs --tail 100 web
docker compose -f deploy/oracle/compose.yaml logs --tail 100 caddy
curl -fsS https://oracle-test.medpally.com/healthz
```

The health response should say the database is `ok`. `ingestion.stale` may be
true on an empty or recently restored database, but this migration keeps the
existing Supabase database so it should normally remain fresh.

Test these user paths before cutover:

1. Landing page and static styling load over HTTPS.
2. Existing email/password login lands on `/feed/`.
3. A disposable new signup enters `/onboarding/`.
4. Feed, Saved, Search, Account, drawer navigation, and logout work.
5. Password-reset email is accepted by Brevo.
6. `/admin/` works for a staff account.
7. `/healthz` returns HTTP 200.

For Google login on the test hostname, temporarily add this authorized
redirect URI in Google Cloud Console:

```text
https://oracle-test.medpally.com/accounts/google/login/callback/
```

Remove the test callback after the migration.

## 8. Start the stack automatically after a VM reboot

The supplied systemd unit assumes the repository lives at `/opt/medpally`:

```bash
sudo cp /opt/medpally/deploy/oracle/medpally.service /etc/systemd/system/medpally.service
sudo systemctl daemon-reload
sudo systemctl enable --now medpally.service
sudo systemctl status medpally.service
```

If the repository is elsewhere, edit `WorkingDirectory` and all compose paths
in the unit before installing it.

Reboot once before cutover to prove recovery is automatic:

```bash
sudo reboot
```

After reconnecting:

```bash
systemctl status medpally.service
docker compose -f /opt/medpally/deploy/oracle/compose.yaml ps
curl -fsS https://oracle-test.medpally.com/healthz
```

## 9. Cut over the production domain

If MedPally already uses a custom domain on Render, the public hostname does
not need to change. Prepare Oracle's environment first:

```dotenv
DOMAIN=medpally.com
DJANGO_ALLOWED_HOSTS=medpally.com,www.medpally.com,oracle-test.medpally.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://medpally.com,https://www.medpally.com,https://oracle-test.medpally.com
SITE_BASE_URL=https://medpally.com
```

Then:

1. Run `deploy/oracle/update.sh` so Caddy is ready for the production hostname.
2. Change the production DNS A record to the reserved OCI public IP.
3. If `www` is used, point it to the same IP or CNAME it to the apex domain.
4. Wait for DNS propagation and Caddy certificate issuance.
5. Confirm the production `/healthz`, login, signup, feed, admin, email, and
   Google OAuth flows.
6. Watch logs during the first hour:

   ```bash
   docker compose -f /opt/medpally/deploy/oracle/compose.yaml logs -f --tail 100
   ```

## 10. Deploy updates from GitHub Actions

The `deploy-oracle.yml` workflow deploys every push to `main`. It uses the
repository secret `ORACLE_DEPLOY_SSH_KEY`, a dedicated SSH key that is limited
to the Oracle deployment user. The workflow syncs source files while preserving
`deploy/oracle/.env`, then builds, starts, and health-checks the stack.

Keep the production environment file on the VM only; it must never be added to
GitHub Actions secrets or committed to the repository.

If the production hostname itself is changing, also update:

- Google OAuth authorized JavaScript origins and callback URI.
- Supabase authentication/site URL settings only if Supabase Auth is later
  introduced; Django allauth currently owns authentication.
- Brevo sender-domain configuration if the email domain changes.
- Sentry allowed domains/environment and any uptime monitor URLs.

## 10. Unhook Render safely

Keep Render available for 24-48 hours after DNS cutover so rollback is quick,
but do not send normal traffic to it. Once Oracle has been stable:

1. In Render Dashboard, open the `medpally` web service.
2. Disable automatic deploys first.
3. Remove the custom domain from Render after confirming Oracle owns the TLS
   certificate and serves the live domain.
4. Suspend the Render service. Leave it suspended for a short rollback window.
5. Delete the Render web service when satisfied with Oracle.
6. Disconnect/delete the Render Blueprint if one is still attached.
7. Remove any Render deploy hooks or GitHub integration that applies only to
   this service.
8. Delete Render's copies of production secrets after the service is deleted.

This branch removes `render.yaml`, so merging it also removes the Render
Blueprint from the repository. Dashboard resources are not deleted by a Git
change; the steps above are still required.

Do **not** delete these GitHub Actions workflows:

- `.github/workflows/nightly.yml`
- `.github/workflows/weekly-featured.yml`
- `.github/workflows/freshness-alarm.yml`

They did not run on Render and should continue using the existing GitHub
secrets and Supabase `DATABASE_URL`.

## 11. Normal deployments and operations

To deploy a new commit:

```bash
cd /opt/medpally
git pull --ff-only
deploy/oracle/update.sh
```

The update script validates Compose, builds the new image, starts it, waits for
the Django health check, and shows logs if the container becomes unhealthy.
Database migrations are applied automatically before Gunicorn starts.

Useful commands:

```bash
# Status
docker compose -f deploy/oracle/compose.yaml ps

# Logs
docker compose -f deploy/oracle/compose.yaml logs -f --tail 200

# Django command
docker compose -f deploy/oracle/compose.yaml exec web python manage.py check

# Restart only the web app
docker compose -f deploy/oracle/compose.yaml restart web

# Show disk use
docker system df

# Remove only unused build cache and untagged images
docker image prune
docker builder prune
```

Apply Ubuntu security updates regularly. Reboot when the kernel requires it;
the systemd unit restores the stack automatically.

## 12. Backups and monitoring

The VM is stateless apart from Caddy's replaceable certificate cache. User and
paper data remain in Supabase, so OCI boot-volume snapshots are useful for fast
host recovery but are not database backups. Enable an OCI boot-volume backup
policy only if it remains inside the Always Free backup/storage allowance shown
in your Console.

Keep these controls:

- Supabase database backups appropriate to the current plan.
- GitHub's independent ingestion freshness workflow.
- Sentry with `SENTRY_ENVIRONMENT=production-oracle` during migration, then
  rename it to `production` when Render is retired if preferred.
- An external HTTPS uptime monitor pointed at `/healthz`.
- OCI budget alerts even when every selected resource is marked Always Free.

## 13. Rollback

During the 24-48 hour rollback window:

1. Resume the Render service.
2. Change the production DNS A/CNAME record back to Render's target.
3. Wait for the 300-second TTL and verify `/healthz`.
4. Diagnose Oracle without changing the Supabase database—both deployments use
   the same schema, so no data copy or replay is required.

After Render is deleted, rollback means provisioning another web host from the
same Git commit. The removed `render.yaml` remains recoverable from Git history.

## Troubleshooting

### Caddy cannot obtain a certificate

Confirm DNS resolves to the reserved OCI IP and OCI allows inbound TCP 80/443.
Check `docker compose -f deploy/oracle/compose.yaml logs caddy`. Do not put a
Cloudflare proxy in front until the first certificate is working unless its TLS
mode is configured correctly.

### Django returns 400 Bad Request

The request hostname is missing from `DJANGO_ALLOWED_HOSTS`. Add only the exact
hostname, then rerun `deploy/oracle/update.sh`.

### CSRF verification fails

Add the full `https://hostname` origin to `DJANGO_CSRF_TRUSTED_ORIGINS`. Do not
include a trailing slash.

### The web container is unhealthy

Run:

```bash
docker compose -f deploy/oracle/compose.yaml logs --tail 200 web
```

The usual causes are an invalid Supabase URL, a password that was not
URL-encoded, an empty host allowlist, or a failed migration.

### The VM runs out of disk space

Inspect with `df -h` and `docker system df`. Prune unused image/build cache, not
named volumes. Caddy's named volumes are small and should normally be retained.
