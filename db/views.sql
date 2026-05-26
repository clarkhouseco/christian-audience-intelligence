-- Christian Audience Intelligence — Dashboard Views
-- Run this in the Supabase SQL editor after schema.sql.
-- These views are what Metabase connects to. Safe to re-run.

-- ─────────────────────────────────────────────────────────────
-- 1. National scores — latest scored date, US only
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_national_scores AS
SELECT
  ts.category,
  REPLACE(ts.category, '_', ' ')            AS category_label,
  ts.composite_score,
  ts.google_score,
  ts.youtube_score,
  ROUND(ts.wow_delta * 100, 1)              AS wow_pct,
  ts.is_emerging,
  ARRAY_TO_STRING(ts.top_terms, ', ')       AS top_terms,
  ts.data_points,
  ts.scored_at
FROM trend_scores ts
JOIN regions r ON ts.region_id = r.id
WHERE r.code = 'US'
  AND ts.scored_at = (SELECT MAX(scored_at) FROM trend_scores WHERE region_id = r.id);


-- ─────────────────────────────────────────────────────────────
-- 2. State scores — latest scored date, state level only
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_state_scores AS
SELECT
  r.name                                    AS state,
  r.code                                    AS state_code,
  ts.category,
  REPLACE(ts.category, '_', ' ')            AS category_label,
  ts.composite_score,
  ts.google_score,
  ROUND(ts.wow_delta * 100, 1)              AS wow_pct,
  ts.is_emerging,
  ARRAY_TO_STRING(ts.top_terms, ', ')       AS top_terms,
  ts.scored_at
FROM trend_scores ts
JOIN regions r ON ts.region_id = r.id
WHERE r.type = 'state'
  AND ts.scored_at = (SELECT MAX(scored_at) FROM trend_scores);


-- ─────────────────────────────────────────────────────────────
-- 3. Emerging — any region, last 30 days
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_emerging AS
SELECT
  ts.category,
  REPLACE(ts.category, '_', ' ')            AS category_label,
  r.name                                    AS region,
  r.type                                    AS region_type,
  ts.composite_score,
  ROUND(ts.wow_delta * 100, 1)              AS wow_pct,
  ARRAY_TO_STRING(ts.top_terms, ', ')       AS top_terms,
  ts.scored_at
FROM trend_scores ts
JOIN regions r ON ts.region_id = r.id
WHERE ts.is_emerging = true
  AND ts.scored_at >= CURRENT_DATE - INTERVAL '30 days'
ORDER BY ts.composite_score DESC, ts.scored_at DESC;


-- ─────────────────────────────────────────────────────────────
-- 4. Category trend over time — national, all scored dates
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_category_trend AS
SELECT
  ts.scored_at,
  ts.category,
  REPLACE(ts.category, '_', ' ')            AS category_label,
  ts.composite_score,
  ts.google_score,
  ts.youtube_score,
  ROUND(ts.wow_delta * 100, 1)              AS wow_pct,
  ts.is_emerging
FROM trend_scores ts
JOIN regions r ON ts.region_id = r.id
WHERE r.code = 'US'
ORDER BY ts.category, ts.scored_at;


-- ─────────────────────────────────────────────────────────────
-- 5. Rising queries — unreviewed, last 60 days
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_rising_queries AS
SELECT
  rq.query,
  st.term                                   AS source_term,
  st.category,
  REPLACE(st.category, '_', ' ')            AS category_label,
  rq.rise_value,
  rq.week_of,
  r.name                                    AS region,
  rq.reviewed
FROM rising_queries rq
JOIN regions r       ON rq.region_id      = r.id
LEFT JOIN search_terms st ON rq.source_term_id = st.id
WHERE rq.week_of >= CURRENT_DATE - INTERVAL '60 days'
ORDER BY rq.week_of DESC, rq.rise_value DESC;


-- ─────────────────────────────────────────────────────────────
-- 6. Top YouTube videos — active, with latest snapshot stats
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_youtube_top AS
SELECT
  yv.video_id,
  yv.channel_id,
  yv.title,
  yv.channel_name,
  yv.category,
  REPLACE(yv.category, '_', ' ')            AS category_label,
  yv.published_at::DATE                     AS published_date,
  yv.is_short,
  yv.duration_seconds,
  ys.view_count,
  ys.like_count,
  ys.comment_count
FROM youtube_videos yv
JOIN youtube_snapshots ys
  ON yv.video_id = ys.video_id
 AND ys.snapshot_date = (SELECT MAX(snapshot_date) FROM youtube_snapshots)
WHERE yv.is_active = true
ORDER BY ys.view_count DESC;


-- ─────────────────────────────────────────────────────────────
-- 7. Search interest by state for a specific category
--    (Use in Metabase with a category filter)
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_state_category_interest AS
SELECT
  r.name                                    AS state,
  r.code                                    AS state_code,
  st.category,
  REPLACE(st.category, '_', ' ')            AS category_label,
  st.term,
  ts.interest_index,
  ts.week_start
FROM trends_snapshots ts
JOIN search_terms st ON ts.term_id  = st.id
JOIN regions      r  ON ts.region_id = r.id
WHERE r.type = 'state'
ORDER BY r.name, st.category, ts.week_start DESC;
