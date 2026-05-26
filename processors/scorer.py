"""
Trend scorer.

Reads raw trends_snapshots and youtube_snapshots, computes momentum scores
per category per region, and writes results to trend_scores.

Scoring model:
  - Google score:    week-over-week % change in search interest, averaged across
                     all terms in the category for a given region. Mapped 0–100.
  - YouTube score:   view velocity (views/day) of recent videos in the category,
                     log-normalized to 0–100 across all categories.
  - Composite:       65% Google + 35% YouTube (YouTube is national only).
  - is_emerging:     composite > 60 AND wow_delta > 15% AND ≥ 2 terms with data.

Run weekly after collectors complete:
    python processors/scorer.py
"""

import sys
import math
import logging
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.client import get_client, upsert, log_run_start, log_run_finish

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Tunable parameters ────────────────────────────────────────────────────────
RECENT_DAYS          = 7      # "this week" window
PRIOR_DAYS           = 14     # "last week" window starts here, ends at RECENT_DAYS
LOOKBACK_DAYS        = 21     # how far back to pull trends data
YT_LOOKBACK_DAYS     = 8      # youtube snapshot window

GOOGLE_WEIGHT        = 0.65
YOUTUBE_WEIGHT       = 0.35

MIN_TERMS            = 2      # minimum terms needed to score a category/region
MIN_BASELINE_INDEX   = 5      # ignore prior windows with near-zero baseline

EMERGE_SCORE         = 60.0   # composite_score threshold for emergence flag
EMERGE_DELTA         = 0.15   # wow_delta (15% growth) threshold for emergence flag
EMERGE_MIN_TERMS     = 2      # min terms with data to flag emergence


# ── Data loading ──────────────────────────────────────────────────────────────

def _load(client, table: str, select: str, extra_filters: list = None, page_size: int = 1000) -> list[dict]:
    """Load all rows with pagination, applying optional chained filters."""
    all_rows: list[dict] = []
    offset = 0
    while True:
        query = client.table(table).select(select).range(offset, offset + page_size - 1)
        for method, col, val in (extra_filters or []):
            query = getattr(query, method)(col, val)
        rows = query.execute().data or []
        all_rows.extend(rows)
        if len(rows) < page_size:
            break   # last page
        offset += page_size
    return all_rows


# ── Google Trends scoring ─────────────────────────────────────────────────────

def _google_scores(snapshots: list[dict], terms_meta: dict) -> dict:
    """
    Compute week-over-week momentum per (category, region_id).

    Returns:
        {(category, region_id): {google_score, wow_delta, data_points, top_terms}}
    """
    today         = date.today()
    recent_start  = str(today - timedelta(days=RECENT_DAYS))
    prior_start   = str(today - timedelta(days=PRIOR_DAYS))

    # Group snapshots by (term_id, region_id)
    by_term_region: dict = defaultdict(list)
    for s in snapshots:
        by_term_region[(s["term_id"], s["region_id"])].append(s)

    # Momentum per term per region
    term_momentums: dict = {}
    for (term_id, region_id), records in by_term_region.items():
        meta = terms_meta.get(term_id)
        if not meta:
            continue

        recent = [r["interest_index"] for r in records if r["week_start"] >= recent_start]
        prior  = [r["interest_index"] for r in records
                  if prior_start <= r["week_start"] < recent_start]

        if not recent or not prior:
            continue

        recent_avg = statistics.mean(recent)
        prior_avg  = statistics.mean(prior)

        if prior_avg >= MIN_BASELINE_INDEX:
            raw = (recent_avg - prior_avg) / prior_avg
            momentum = max(-1.0, min(1.0, raw))
        else:
            momentum = 0.0

        term_momentums[(term_id, region_id)] = {
            "momentum":    momentum,
            "recent_avg":  recent_avg,
            "category":    meta["category"],
            "term":        meta["term"],
            "is_seasonal": meta["is_seasonal"],
            "data_points": len(records),
        }

    # Aggregate by (category, region_id)
    by_cat_region: dict = defaultdict(list)
    for (term_id, region_id), data in term_momentums.items():
        by_cat_region[(data["category"], region_id)].append(data)

    results: dict = {}
    for (category, region_id), terms in by_cat_region.items():
        if len(terms) < MIN_TERMS:
            continue

        avg_momentum = statistics.mean(t["momentum"] for t in terms)

        # Map [-1, 1] → [0, 100]   (0 change = 50, +100% = 100, -100% = 0)
        google_score = round((avg_momentum + 1) / 2 * 100, 2)

        top_terms = [
            t["term"]
            for t in sorted(terms, key=lambda x: x["momentum"], reverse=True)[:3]
        ]

        results[(category, region_id)] = {
            "google_score": google_score,
            "wow_delta":    round(avg_momentum, 4),
            "data_points":  len(terms),
            "top_terms":    top_terms,
        }

    return results


# ── YouTube scoring ───────────────────────────────────────────────────────────

def _youtube_scores(yt_snapshots: list[dict], videos_meta: dict) -> dict:
    """
    Compute view velocity per category, log-normalized to 0–100.

    Returns:
        {category: youtube_score}
    """
    # Group snapshots by video_id
    by_video: dict = defaultdict(list)
    for s in yt_snapshots:
        meta = videos_meta.get(s["video_id"])
        if meta and meta.get("is_active") and meta.get("category"):
            by_video[s["video_id"]].append(s)

    # View velocity per video
    video_velocity: dict = {}
    for vid_id, records in by_video.items():
        records.sort(key=lambda x: x["snapshot_date"])
        if len(records) < 2:
            # Only 1 snapshot — use raw view count as a proxy signal
            video_velocity[vid_id] = float(records[0]["view_count"] or 0)
        else:
            latest  = float(records[-1]["view_count"] or 0)
            oldest  = float(records[0]["view_count"]  or 0)
            days    = max(1, len(records) - 1)
            video_velocity[vid_id] = max(0.0, (latest - oldest) / days)

    if not video_velocity:
        return {}

    # Group by category
    by_category: dict = defaultdict(list)
    for vid_id, vel in video_velocity.items():
        cat = videos_meta[vid_id]["category"]
        by_category[cat].append(vel)

    # Log-normalize: log1p compresses viral outliers so one massive video
    # doesn't make every other category look like zero.
    all_vels = [v for vals in by_category.values() for v in vals]
    max_log  = math.log1p(max(all_vels)) if all_vels else 1.0

    yt_scores: dict = {}
    for category, velocities in by_category.items():
        avg = statistics.mean(velocities)
        yt_scores[category] = round(
            min(100.0, math.log1p(avg) / max_log * 100), 2
        )

    return yt_scores


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    client = get_client()
    run_id = log_run_start("scorer")

    try:
        today           = date.today()
        lookback_str    = str(today - timedelta(days=LOOKBACK_DAYS))
        yt_lookback_str = str(today - timedelta(days=YT_LOOKBACK_DAYS))

        # ── Load data ─────────────────────────────────────────────────────────
        log.info("Loading search terms...")
        terms_rows = _load(client, "search_terms",
                           "id, term, category, is_seasonal, is_active",
                           [("eq", "is_active", True)])
        terms_meta = {r["id"]: r for r in terms_rows}
        log.info("  %d active terms", len(terms_meta))

        log.info("Loading trends snapshots (last %d days)...", LOOKBACK_DAYS)
        snapshots = _load(
            client, "trends_snapshots",
            "term_id, region_id, week_start, interest_index",
            [("gte", "week_start", lookback_str)],
        )
        log.info("  %d snapshot rows", len(snapshots))

        log.info("Loading YouTube videos...")
        videos_rows = _load(client, "youtube_videos",
                            "video_id, category, is_active",
                            [("eq", "is_active", True)])
        videos_meta = {r["video_id"]: r for r in videos_rows}
        log.info("  %d active videos", len(videos_meta))

        log.info("Loading YouTube snapshots (last %d days)...", YT_LOOKBACK_DAYS)
        yt_snapshots = _load(
            client, "youtube_snapshots",
            "video_id, snapshot_date, view_count",
            [("gte", "snapshot_date", yt_lookback_str)],
        )
        log.info("  %d YouTube snapshot rows", len(yt_snapshots))

        # ── Score ─────────────────────────────────────────────────────────────
        log.info("Computing Google Trends scores...")
        g_scores = _google_scores(snapshots, terms_meta)
        log.info("  %d (category × region) pairs scored", len(g_scores))

        log.info("Computing YouTube scores...")
        y_scores = _youtube_scores(yt_snapshots, videos_meta)
        log.info("  %d categories scored from YouTube", len(y_scores))

        # ── Build rows ────────────────────────────────────────────────────────
        today_str   = str(today)
        score_rows  = []

        for (category, region_id), g in g_scores.items():
            yt = y_scores.get(category, 0.0)

            composite = round(
                g["google_score"] * GOOGLE_WEIGHT + yt * YOUTUBE_WEIGHT, 2
            )

            is_emerging = (
                composite  > EMERGE_SCORE
                and g["wow_delta"] > EMERGE_DELTA
                and g["data_points"] >= EMERGE_MIN_TERMS
            )

            score_rows.append({
                "category":        category,
                "region_id":       region_id,
                "scored_at":       today_str,
                "composite_score": composite,
                "google_score":    g["google_score"],
                "youtube_score":   yt,
                "wow_delta":       g["wow_delta"],
                "is_emerging":     is_emerging,
                "top_terms":       g["top_terms"],
                "data_points":     g["data_points"],
            })

        # ── Write ─────────────────────────────────────────────────────────────
        if not score_rows:
            log.warning("No score rows generated — check that collectors have run.")
            log_run_finish(run_id, "partial", 0,
                           notes="No score rows — insufficient data")
            return

        log.info("Writing %d score rows...", len(score_rows))
        written = upsert(
            "trend_scores",
            score_rows,
            on_conflict="category,region_id,scored_at",
        )
        log.info("Done. %d rows written.", written)

        # Summary: show emerging categories nationally
        national_emerging = [
            r for r in score_rows
            if r["is_emerging"] and r["region_id"] == _national_id(client)
        ]
        if national_emerging:
            national_emerging.sort(key=lambda x: x["composite_score"], reverse=True)
            log.info("── Nationally emerging categories ──")
            for r in national_emerging:
                log.info(
                    "  %-35s score=%.1f  Δ=+%.0f%%  terms=%s",
                    r["category"], r["composite_score"],
                    r["wow_delta"] * 100, r["top_terms"]
                )
        else:
            log.info("No nationally emerging categories detected this run.")

        log_run_finish(run_id, "success", written)

    except Exception as e:
        log.error("Fatal: %s", e, exc_info=True)
        log_run_finish(run_id, "failed", errors=str(e))
        sys.exit(1)


def _national_id(client) -> int:
    rows = client.table("regions").select("id").eq("code", "US").limit(1).execute().data
    return rows[0]["id"] if rows else None


if __name__ == "__main__":
    main()
