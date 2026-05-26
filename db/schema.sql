-- Christian Audience Intelligence — Supabase Schema
-- Run this once in the Supabase SQL editor to set up all tables.
-- Safe to re-run: uses CREATE TABLE IF NOT EXISTS.

-- ─────────────────────────────────────────────
-- Geographic reference
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS regions (
  id        SERIAL PRIMARY KEY,
  type      TEXT    NOT NULL CHECK (type IN ('national', 'state', 'dma')),
  code      TEXT    NOT NULL UNIQUE,
  name      TEXT    NOT NULL
);

-- National row (always exists, used for rollup scores)
INSERT INTO regions (type, code, name)
VALUES ('national', 'US', 'United States')
ON CONFLICT (code) DO NOTHING;

-- ─────────────────────────────────────────────
-- Search terms + category mapping
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS search_terms (
  id         SERIAL  PRIMARY KEY,
  term       TEXT    NOT NULL UNIQUE,
  category   TEXT    NOT NULL,
  source     TEXT    NOT NULL CHECK (source IN ('google_trends', 'youtube', 'both')),
  is_anchor  BOOLEAN NOT NULL DEFAULT false,
  is_seasonal BOOLEAN NOT NULL DEFAULT false,
  is_active  BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────
-- Google Trends — weekly snapshots per term per region
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trends_snapshots (
  id              SERIAL PRIMARY KEY,
  term_id         INT    NOT NULL REFERENCES search_terms(id) ON DELETE CASCADE,
  region_id       INT    NOT NULL REFERENCES regions(id)      ON DELETE CASCADE,
  week_start      DATE   NOT NULL,
  interest_index  INT    CHECK (interest_index BETWEEN 0 AND 100),
  collected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (term_id, region_id, week_start)
);

CREATE INDEX IF NOT EXISTS idx_trends_snapshots_term_region
  ON trends_snapshots (term_id, region_id, week_start DESC);

-- ─────────────────────────────────────────────
-- Google Trends — rising related queries
-- These are discovered from related_queries() and fed back into search_terms
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rising_queries (
  id             SERIAL PRIMARY KEY,
  query          TEXT   NOT NULL,
  source_term_id INT    REFERENCES search_terms(id) ON DELETE SET NULL,
  region_id      INT    NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  rise_value     TEXT,
  week_of        DATE   NOT NULL,
  collected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed       BOOLEAN NOT NULL DEFAULT false,
  UNIQUE (query, region_id, week_of)
);

-- ─────────────────────────────────────────────
-- YouTube — videos being tracked
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS youtube_videos (
  id             SERIAL PRIMARY KEY,
  video_id       TEXT   NOT NULL UNIQUE,
  title          TEXT,
  channel_id     TEXT,
  channel_name   TEXT,
  published_at   TIMESTAMPTZ,
  search_term_id INT    REFERENCES search_terms(id) ON DELETE SET NULL,
  category       TEXT,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  discovered_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_youtube_videos_category
  ON youtube_videos (category, is_active);

-- ─────────────────────────────────────────────
-- YouTube — daily engagement snapshots
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS youtube_snapshots (
  id            SERIAL PRIMARY KEY,
  video_id      TEXT   NOT NULL REFERENCES youtube_videos(video_id) ON DELETE CASCADE,
  snapshot_date DATE   NOT NULL,
  collected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  view_count    BIGINT,
  like_count    INT,
  comment_count INT,
  UNIQUE (video_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_youtube_snapshots_video_date
  ON youtube_snapshots (video_id, snapshot_date DESC);

-- ─────────────────────────────────────────────
-- Computed trend scores — written by scorer.py, read by dashboard
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trend_scores (
  id                  SERIAL  PRIMARY KEY,
  category            TEXT    NOT NULL,
  region_id           INT     NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  scored_at           DATE    NOT NULL,
  composite_score     NUMERIC(6,2),
  google_score        NUMERIC(6,2),
  youtube_score       NUMERIC(6,2),
  wow_delta           NUMERIC(8,4),   -- week-over-week % change
  is_emerging         BOOLEAN NOT NULL DEFAULT false,
  top_terms           TEXT[],         -- up to 3 terms that drove this score
  data_points         INT,            -- how many terms contributed
  UNIQUE (category, region_id, scored_at)
);

CREATE INDEX IF NOT EXISTS idx_trend_scores_category_date
  ON trend_scores (category, scored_at DESC);

CREATE INDEX IF NOT EXISTS idx_trend_scores_emerging
  ON trend_scores (scored_at DESC, is_emerging)
  WHERE is_emerging = true;

-- ─────────────────────────────────────────────
-- Collection run log — lightweight audit trail
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS collection_runs (
  id           SERIAL PRIMARY KEY,
  source       TEXT   NOT NULL CHECK (source IN ('google_trends', 'youtube', 'scorer')),
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  status       TEXT   NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'partial', 'failed')),
  rows_written INT,
  errors       TEXT,
  notes        TEXT
);
