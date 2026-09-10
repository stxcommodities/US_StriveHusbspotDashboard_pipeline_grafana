# Pipeline ETL → Grafana

Pulls deals from a HubSpot pipeline on a schedule and writes them into
Postgres. Grafana visualizes that Postgres table — Grafana never talks to
HubSpot directly.

## This deployment's specifics (STX / TDE)

- **Host**: `tde-pgsql.stxaws.net`, **database**: `tde`
- **Role**: `hubspot_us`, **schema**: `hubspot_us` (role has `CREATE, USAGE`
  on it; the `reporting` role already has `SELECT` on at least one table
  there, which is presumably how Grafana's existing "TDE" data source will
  read these tables too — confirm with the platform team that new tables
  inherit that same grant automatically, or that they'll grant `SELECT` on
  `deals_snapshot` / `deal_stage_events` to `reporting` after the first run)
- Credentials live in AWS Secrets Manager as **`tde-hubspot_us`** — pull the
  password from there at runtime, never hardcode or paste it anywhere
  (chat, code, commit messages).
- `DB_SCHEMA=hubspot_us` is what points this code at the right schema (see
  `config.py` / `db.py`) — it's already set in `k8s/cronjob.yaml`.

## What you need before starting

1. **A Postgres database** you can create tables in (existing shared
   instance, or a new one) — get a connection string. *(Done — see above.)*
2. **Postgres added as a Grafana data source**, pointing at that database.
   *(Done — the existing "TDE" data source in Grafana already points at
   `tde-pgsql.stxaws.net`.)*
3. **A HubSpot private app token**, read-only scopes:
   `crm.objects.deals.read`, `crm.schemas.deals.read`.
4. **A place to run the CronJob** — your existing k8s namespace, container
   registry, and however your team manages secrets (external-secrets
   operator, sealed-secrets, vault — avoid plaintext Secret manifests).
5. **`HUBSPOT_PIPELINE_ID`** confirmed for your real portal (defaults to
   `default` / "Strive - US", which was empty in the portal this was tested
   against — verify before relying on it).

## Steps

1. **Create the schema.** Run `schema.sql` against your Postgres database
   once (`etl.py` also runs it automatically on every start, so this step
   is optional but useful to sanity-check DB access up front):
   ```bash
   psql "$DATABASE_URL" -f schema.sql
   ```
   Note: this only creates tables (no `CREATE SCHEMA`) — they land in
   whatever `DB_SCHEMA` points `search_path` to.

2. **Test the ETL script locally.**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   export HUBSPOT_PRIVATE_APP_TOKEN=...
   # Get the real password from AWS Secrets Manager secret "tde-hubspot_us" --
   # don't hardcode it here or paste it anywhere outside your own shell.
   export DATABASE_URL=postgresql://hubspot_us:<password-from-secrets-manager>@tde-pgsql.stxaws.net:5432/tde
   export DB_SCHEMA=hubspot_us
   export HUBSPOT_PIPELINE_ID=default   # or your real pipeline ID
   python etl.py
   ```
   Check `deals_snapshot` and `deal_stage_events` in Postgres have rows.

3. **Build and push the image.**
   ```bash
   docker build -t <your-registry>/pipeline-etl:latest .
   docker push <your-registry>/pipeline-etl:latest
   ```

4. **Create the secret** (see `k8s/db-secret.example.yaml` — prefer your
   secrets-manager operator over the plaintext fallback shown there).

5. **Deploy the CronJob.** Fill in the `CHANGE_ME` placeholders in
   `k8s/cronjob.yaml` (namespace, image), then:
   ```bash
   kubectl apply -f k8s/db-secret.yaml   # your filled-in copy
   kubectl apply -f k8s/cronjob.yaml
   ```
   Check it ran: `kubectl get jobs -n <namespace>` and
   `kubectl logs -n <namespace> job/<job-name>`.

6. **Import the Grafana dashboard.** In Grafana: Dashboards → New → Import
   → upload `grafana/dashboard.json` (or paste its contents) → select your
   Postgres data source when prompted. Adjust the time range picker to
   control the "what changed" window — no code change needed to look back
   further than a week.

7. **Sanity check the numbers** against what you see in HubSpot directly
   before trusting this for a real review.

## Files

| File | What it's for |
|---|---|
| `hubspot_client.py` | HubSpot API calls (pipelines, deals, stage history) |
| `config.py` | All settings, from environment variables |
| `db.py` | Postgres upsert/insert helpers |
| `etl.py` | The script that runs each cycle |
| `schema.sql` | Table definitions |
| `Dockerfile` | Container image for the CronJob |
| `k8s/cronjob.yaml` | Kubernetes CronJob, runs every 15 min by default |
| `k8s/db-secret.example.yaml` | Secret template — fill in your own, don't commit it |
| `grafana/dashboard.json` | Importable dashboard: KPIs, deals-by-stage, change log |
