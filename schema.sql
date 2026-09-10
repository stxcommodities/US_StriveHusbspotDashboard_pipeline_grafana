-- Pipeline dashboard ETL schema.
--
-- Two tables, on purpose:
--   deals_snapshot     current state of every deal (one row per deal,
--                       overwritten each run) -- what Grafana's "deals by
--                       stage" / KPI panels query.
--   deal_stage_events   append-only log of created / stage-move / closed
--                       events -- what Grafana's "what changed" panel
--                       queries, using Grafana's own time range picker
--                       against changed_at (no hardcoded "7 days" needed;
--                       whatever range the viewer picks in Grafana works).
--
-- Run this once against your Postgres instance before the first ETL run
-- (etl.py also runs it automatically on startup via ensure_schema()).
--
-- No CREATE SCHEMA here on purpose -- your role likely only has CREATE on a
-- schema someone else already provisioned. db.py sets search_path to
-- config.DB_SCHEMA on connect, so these CREATE TABLE statements land there.

CREATE TABLE IF NOT EXISTS deals_snapshot (
    deal_id         TEXT PRIMARY KEY,
    deal_name       TEXT NOT NULL,
    pipeline_id     TEXT NOT NULL,
    pipeline_label  TEXT NOT NULL,
    stage_id        TEXT NOT NULL,
    stage_label     TEXT NOT NULL,
    is_closed       BOOLEAN NOT NULL DEFAULT FALSE,
    is_won          BOOLEAN NOT NULL DEFAULT FALSE,
    amount          NUMERIC,
    weighted_amount NUMERIC,
    probability     NUMERIC,
    owner_id        TEXT,
    create_date     TIMESTAMPTZ,
    close_date      TIMESTAMPTZ,
    last_modified   TIMESTAMPTZ,
    snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_deals_snapshot_pipeline ON deals_snapshot (pipeline_id);

CREATE TABLE IF NOT EXISTS deal_stage_events (
    id           BIGSERIAL PRIMARY KEY,
    deal_id      TEXT NOT NULL,
    deal_name    TEXT NOT NULL,
    pipeline_id  TEXT NOT NULL,
    event_type   TEXT NOT NULL,   -- 'created' | 'stage_move' | 'closed'
    stage_id     TEXT NOT NULL,
    stage_label  TEXT NOT NULL,
    amount       NUMERIC,
    changed_at   TIMESTAMPTZ NOT NULL,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (deal_id, event_type, changed_at)
);

CREATE INDEX IF NOT EXISTS idx_deal_stage_events_changed_at ON deal_stage_events (changed_at);
CREATE INDEX IF NOT EXISTS idx_deal_stage_events_pipeline ON deal_stage_events (pipeline_id);
