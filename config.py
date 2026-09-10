"""
Configuration for the pipeline dashboard, loaded entirely from environment
variables so nothing sensitive (or environment-specific) is hardcoded or
committed to source control.

Required at runtime:
    HUBSPOT_PRIVATE_APP_TOKEN   HubSpot private app access token.
                                Required scopes: crm.objects.deals.read,
                                crm.schemas.deals.read, crm.objects.owners.read
                                Provide this via a Kubernetes Secret (see
                                k8s/secret.example.yaml) or your team's
                                secrets manager -- never commit a real token.

Optional (sensible defaults below):
    HUBSPOT_PIPELINE_ID         Which pipeline to show. Defaults to "default",
                                 which is HubSpot's internal ID for the
                                 "Strive - US" pipeline in this account. If
                                 your production portal uses a different
                                 pipeline for the Monday review, look up its
                                 ID in HubSpot (Settings -> Objects -> Deals ->
                                 Pipelines -> ... -> the ID in the URL) and
                                 override it here.
    HUBSPOT_OWNER_SCOPE          "all" (default) or a comma-separated list of
                                 HubSpot owner IDs to filter to.
    CHANGE_WINDOW_DAYS           How many days back counts as "this week" for
                                 the change feed. Default 7.
    CACHE_TTL_SECONDS            How long fetched HubSpot data is cached
                                 before the app re-queries the API. Default
                                 900 (15 minutes). Lower = fresher data, more
                                 API calls; raise this if you hit HubSpot
                                 rate limits with many concurrent viewers.
    HUBSPOT_API_BASE             Override for testing against a mock server.

ETL / database (only needed by etl.py, not the Streamlit app):
    DATABASE_URL                 Postgres connection string, e.g.
                                 postgresql://user:pass@host:5432/dbname
                                 Provide via a Kubernetes Secret -- see
                                 k8s/db-secret.example.yaml.
    DB_SCHEMA                    Postgres schema to create/use tables in.
                                 Defaults to "public". Set this to your
                                 dedicated schema (e.g. the one your platform
                                 team provisions per-role) so tables land
                                 there instead of the default schema.
"""

import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


HUBSPOT_PRIVATE_APP_TOKEN = os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN", "")

HUBSPOT_PIPELINE_ID = os.environ.get("HUBSPOT_PIPELINE_ID", "default")

_owner_scope_raw = os.environ.get("HUBSPOT_OWNER_SCOPE", "all").strip()
HUBSPOT_OWNER_IDS = (
    None
    if _owner_scope_raw.lower() == "all" or not _owner_scope_raw
    else [o.strip() for o in _owner_scope_raw.split(",") if o.strip()]
)

CHANGE_WINDOW_DAYS = _env_int("CHANGE_WINDOW_DAYS", 7)
CACHE_TTL_SECONDS = _env_int("CACHE_TTL_SECONDS", 900)

HUBSPOT_API_BASE = os.environ.get("HUBSPOT_API_BASE", "https://api.hubapi.com")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Postgres identifiers can't be parameterized with placeholders, so this is
# validated strictly (letters, digits, underscore only) before ever being
# interpolated into SQL, to rule out injection via a bad env var.
_DB_SCHEMA_RAW = os.environ.get("DB_SCHEMA", "public").strip()
if not _DB_SCHEMA_RAW.replace("_", "").isalnum():
    raise ValueError(
        f"DB_SCHEMA '{_DB_SCHEMA_RAW}' is invalid -- use only letters, "
        "digits, and underscores."
    )
DB_SCHEMA = _DB_SCHEMA_RAW

DEAL_PROPERTIES = [
    "dealname",
    "amount",
    "amount_in_home_currency",
    "hs_projected_amount",
    "hs_projected_amount_in_home_currency",
    "hs_deal_stage_probability",
    "dealstage",
    "pipeline",
    "createdate",
    "closedate",
    "hs_lastmodifieddate",
    "hubspot_owner_id",
]
