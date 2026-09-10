"""
Postgres access for the ETL job. Deliberately plain psycopg2 -- this writes
a handful of rows every run, an ORM would be more ceremony than the job
needs.
"""

from __future__ import annotations

import os

import psycopg2
import psycopg2.extras

import config


def get_conn():
    if not config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Provide it via a Kubernetes Secret -- "
            "see k8s/db-secret.example.yaml."
        )
    # Pin the session's search_path to the configured schema (falling back to
    # public) rather than schema-qualifying every query by hand. config.py
    # already validates DB_SCHEMA is alnum/underscore only, so this is safe
    # to interpolate. Note: this requires USAGE on that schema for the
    # connecting role -- if your role's default schema already matches its
    # username (a common per-tenant pattern), this is redundant but harmless.
    return psycopg2.connect(
        config.DATABASE_URL,
        options=f"-c search_path={config.DB_SCHEMA},public",
    )


def ensure_schema(conn) -> None:
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        ddl = f.read()
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()


def upsert_deal_snapshot(conn, row: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deals_snapshot (
                deal_id, deal_name, pipeline_id, pipeline_label,
                stage_id, stage_label, is_closed, is_won,
                amount, weighted_amount, probability, owner_id,
                create_date, close_date, last_modified, snapshot_at
            ) VALUES (
                %(deal_id)s, %(deal_name)s, %(pipeline_id)s, %(pipeline_label)s,
                %(stage_id)s, %(stage_label)s, %(is_closed)s, %(is_won)s,
                %(amount)s, %(weighted_amount)s, %(probability)s, %(owner_id)s,
                %(create_date)s, %(close_date)s, %(last_modified)s, now()
            )
            ON CONFLICT (deal_id) DO UPDATE SET
                deal_name = EXCLUDED.deal_name,
                pipeline_id = EXCLUDED.pipeline_id,
                pipeline_label = EXCLUDED.pipeline_label,
                stage_id = EXCLUDED.stage_id,
                stage_label = EXCLUDED.stage_label,
                is_closed = EXCLUDED.is_closed,
                is_won = EXCLUDED.is_won,
                amount = EXCLUDED.amount,
                weighted_amount = EXCLUDED.weighted_amount,
                probability = EXCLUDED.probability,
                owner_id = EXCLUDED.owner_id,
                create_date = EXCLUDED.create_date,
                close_date = EXCLUDED.close_date,
                last_modified = EXCLUDED.last_modified,
                snapshot_at = now()
            """,
            row,
        )


def insert_event_if_new(conn, row: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deal_stage_events (
                deal_id, deal_name, pipeline_id, event_type,
                stage_id, stage_label, amount, changed_at
            ) VALUES (
                %(deal_id)s, %(deal_name)s, %(pipeline_id)s, %(event_type)s,
                %(stage_id)s, %(stage_label)s, %(amount)s, %(changed_at)s
            )
            ON CONFLICT (deal_id, event_type, changed_at) DO NOTHING
            """,
            row,
        )


def remove_stale_deals(conn, pipeline_id: str, seen_deal_ids: list[str]) -> int:
    """Delete snapshot rows for deals that no longer appear in this pipeline
    (moved to another pipeline, deleted, or filtered out by owner scope)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM deals_snapshot
            WHERE pipeline_id = %s AND NOT (deal_id = ANY(%s))
            """,
            (pipeline_id, seen_deal_ids or [""]),
        )
        return cur.rowcount
