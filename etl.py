"""
Pulls deals from a HubSpot pipeline and writes them into Postgres for
Grafana to visualize.

Meant to run on a schedule (see k8s/cronjob.yaml) rather than continuously --
each run is a full, idempotent sync: it upserts the current state of every
deal into `deals_snapshot`, and appends any created/stage-move/closed events
that happened since the last run into `deal_stage_events` (skipping ones
already recorded, so re-running never double-counts).

Run manually:
    export HUBSPOT_PRIVATE_APP_TOKEN=...
    export DATABASE_URL=postgresql://user:pass@host:5432/dbname
    python etl.py
"""

from __future__ import annotations

import logging
import sys

import config
import db
import hubspot_client as hs

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("etl")


def run() -> None:
    pipelines = hs.get_pipelines()
    pipeline = next(
        (p for p in pipelines if p.pipeline_id == config.HUBSPOT_PIPELINE_ID), None
    )
    if pipeline is None:
        available = ", ".join(f"{p.label} ({p.pipeline_id})" for p in pipelines)
        raise SystemExit(
            f"Pipeline id '{config.HUBSPOT_PIPELINE_ID}' not found. "
            f"Available: {available}"
        )

    log.info("Fetching deals for pipeline '%s' (%s)", pipeline.label, pipeline.pipeline_id)
    deals = hs.search_deals(config.HUBSPOT_PIPELINE_ID, config.HUBSPOT_OWNER_IDS)
    log.info("Fetched %d deals", len(deals))

    history = hs.get_stage_change_history([d.deal_id for d in deals])

    conn = db.get_conn()
    try:
        db.ensure_schema(conn)

        for deal in deals:
            stage = next(
                (s for s in pipeline.stages if s.stage_id == deal.stage_id), None
            )
            is_closed = bool(stage and stage.is_closed)
            is_won = bool(stage and stage.is_closed and stage.probability >= 1)

            db.upsert_deal_snapshot(
                conn,
                {
                    "deal_id": deal.deal_id,
                    "deal_name": deal.name,
                    "pipeline_id": pipeline.pipeline_id,
                    "pipeline_label": pipeline.label,
                    "stage_id": deal.stage_id,
                    "stage_label": pipeline.stage_label(deal.stage_id),
                    "is_closed": is_closed,
                    "is_won": is_won,
                    "amount": deal.amount,
                    "weighted_amount": deal.weighted_amount,
                    "probability": deal.probability,
                    "owner_id": deal.owner_id,
                    "create_date": deal.create_date,
                    "close_date": deal.close_date,
                    "last_modified": deal.last_modified,
                },
            )

            if deal.create_date:
                db.insert_event_if_new(
                    conn,
                    {
                        "deal_id": deal.deal_id,
                        "deal_name": deal.name,
                        "pipeline_id": pipeline.pipeline_id,
                        "event_type": "created",
                        "stage_id": deal.stage_id,
                        "stage_label": pipeline.stage_label(deal.stage_id),
                        "amount": deal.amount,
                        "changed_at": deal.create_date,
                    },
                )

            # Skip the first history entry -- that's the deal landing in its
            # initial stage on creation, already captured by "created" above.
            for entry in history.get(deal.deal_id, [])[1:]:
                ts = hs.parse_dt(entry.get("timestamp"))
                if not ts:
                    continue
                db.insert_event_if_new(
                    conn,
                    {
                        "deal_id": deal.deal_id,
                        "deal_name": deal.name,
                        "pipeline_id": pipeline.pipeline_id,
                        "event_type": "closed" if entry["value"] in (
                            s.stage_id for s in pipeline.stages if s.is_closed
                        ) else "stage_move",
                        "stage_id": entry["value"],
                        "stage_label": pipeline.stage_label(entry["value"]),
                        "amount": deal.amount,
                        "changed_at": ts,
                    },
                )

        removed = db.remove_stale_deals(
            conn, pipeline.pipeline_id, [d.deal_id for d in deals]
        )
        if removed:
            log.info("Removed %d stale snapshot rows no longer in this pipeline", removed)

        conn.commit()
        log.info("ETL run complete: %d deals synced", len(deals))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        run()
    except hs.HubSpotAuthError as e:
        log.error("Authentication problem: %s", e)
        sys.exit(1)
    except hs.HubSpotAPIError as e:
        log.error("HubSpot API error: %s", e)
        sys.exit(1)
