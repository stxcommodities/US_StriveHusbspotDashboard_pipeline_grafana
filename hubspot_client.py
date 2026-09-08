"""
Thin client around the HubSpot CRM v3 API for the deals this dashboard needs.

Deliberately dependency-light (just `requests`) so it's easy to audit and run
anywhere -- a container, a notebook, a cron job -- without dragging in the
full HubSpot SDK.

All calls use a private app access token (Bearer auth). See config.py for
where that token comes from and the scopes it needs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import requests

import config


class HubSpotAuthError(RuntimeError):
    """Raised when the API rejects the configured token (expired / missing scopes)."""


class HubSpotAPIError(RuntimeError):
    """Raised for any other non-2xx response from the HubSpot API."""


def _session() -> requests.Session:
    if not config.HUBSPOT_PRIVATE_APP_TOKEN:
        raise HubSpotAuthError(
            "HUBSPOT_PRIVATE_APP_TOKEN is not set. Provide it via a Kubernetes "
            "Secret or your secrets manager -- see k8s/secret.example.yaml."
        )
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": f"Bearer {config.HUBSPOT_PRIVATE_APP_TOKEN}",
            "Content-Type": "application/json",
        }
    )
    return s


def _raise_for_status(resp: requests.Response) -> None:
    if resp.status_code in (401, 403):
        raise HubSpotAuthError(
            f"HubSpot rejected the request ({resp.status_code}). "
            "The token may be expired, revoked, or missing a required scope "
            f"(crm.objects.deals.read, crm.schemas.deals.read). Body: {resp.text[:300]}"
        )
    if not resp.ok:
        raise HubSpotAPIError(f"HubSpot API error {resp.status_code}: {resp.text[:500]}")


@dataclass
class PipelineStage:
    stage_id: str
    label: str
    display_order: int
    is_closed: bool
    probability: float


@dataclass
class Pipeline:
    pipeline_id: str
    label: str
    stages: list[PipelineStage] = field(default_factory=list)

    def stage_label(self, stage_id: str) -> str:
        for stage in self.stages:
            if stage.stage_id == stage_id:
                return stage.label
        return stage_id  # fall back to the raw ID rather than hide the mismatch


@dataclass
class Deal:
    deal_id: str
    name: str
    pipeline_id: str
    stage_id: str
    amount: float | None
    weighted_amount: float | None
    probability: float | None
    owner_id: str | None
    create_date: dt.datetime | None
    close_date: dt.datetime | None
    last_modified: dt.datetime | None


def get_pipelines() -> list[Pipeline]:
    """Fetch every deal pipeline with its full, untruncated stage list.

    This is the fix for the truncation problem we hit going through the
    generic property-enum lookup: the dedicated Pipelines API returns each
    pipeline's stages directly, scoped to that pipeline, with no cap.
    """
    resp = _session().get(f"{config.HUBSPOT_API_BASE}/crm/v3/pipelines/deals")
    _raise_for_status(resp)
    pipelines = []
    for p in resp.json().get("results", []):
        stages = [
            PipelineStage(
                stage_id=s["id"],
                label=s["label"],
                display_order=s.get("displayOrder", 0),
                is_closed=s.get("metadata", {}).get("isClosed") == "true",
                probability=float(s.get("metadata", {}).get("probability", 0) or 0),
            )
            for s in p.get("stages", [])
        ]
        stages.sort(key=lambda s: s.display_order)
        pipelines.append(Pipeline(pipeline_id=p["id"], label=p["label"], stages=stages))
    return pipelines


def parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# Kept for compatibility with the sibling Streamlit package, which imports
# this private name -- both point at the same function.
_parse_dt = parse_dt


def _parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def search_deals(pipeline_id: str, owner_ids: list[str] | None = None) -> list[Deal]:
    """Fetch every deal in a pipeline, paginating through the search API."""
    deals: list[Deal] = []
    after: str | None = None
    filters = [{"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id}]
    if owner_ids:
        filters.append(
            {"propertyName": "hubspot_owner_id", "operator": "IN", "values": owner_ids}
        )

    while True:
        body = {
            "filterGroups": [{"filters": filters}],
            "properties": config.DEAL_PROPERTIES,
            "limit": 100,
        }
        if after:
            body["after"] = after

        resp = _session().post(
            f"{config.HUBSPOT_API_BASE}/crm/v3/objects/deals/search", json=body
        )
        _raise_for_status(resp)
        payload = resp.json()

        for record in payload.get("results", []):
            props = record.get("properties", {})
            weighted = _parse_float(props.get("hs_projected_amount"))
            amount = _parse_float(props.get("amount"))
            deals.append(
                Deal(
                    deal_id=record["id"],
                    name=props.get("dealname") or "(unnamed deal)",
                    pipeline_id=props.get("pipeline") or pipeline_id,
                    stage_id=props.get("dealstage") or "",
                    amount=amount,
                    weighted_amount=weighted if weighted is not None else amount,
                    probability=_parse_float(props.get("hs_deal_stage_probability")),
                    owner_id=props.get("hubspot_owner_id") or None,
                    create_date=_parse_dt(props.get("createdate")),
                    close_date=_parse_dt(props.get("closedate")),
                    last_modified=_parse_dt(props.get("hs_lastmodifieddate")),
                )
            )

        after = payload.get("paging", {}).get("next", {}).get("after")
        if not after:
            break

    return deals


def get_stage_change_history(deal_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch dealstage change history for a batch of deals.

    Returns {deal_id: [{"value": stage_id, "timestamp": iso8601}, ...]}
    ordered oldest-to-newest, so callers can tell exactly when a deal moved
    stage rather than approximating it from hs_lastmodifieddate (which also
    changes for unrelated edits).
    """
    if not deal_ids:
        return {}

    history: dict[str, list[dict]] = {}
    # HubSpot's batch read caps at 100 IDs per call.
    for i in range(0, len(deal_ids), 100):
        chunk = deal_ids[i : i + 100]
        body = {
            "inputs": [{"id": d} for d in chunk],
            "propertiesWithHistory": ["dealstage"],
        }
        resp = _session().post(
            f"{config.HUBSPOT_API_BASE}/crm/v3/objects/deals/batch/read", json=body
        )
        _raise_for_status(resp)
        for record in resp.json().get("results", []):
            deal_id = record["id"]
            stage_history = (
                record.get("propertiesWithHistory", {}).get("dealstage", [])
            )
            # HubSpot returns newest-first; flip to chronological order.
            history[deal_id] = list(reversed(stage_history))
    return history
