"""
YouTube Data API v3 collector.

Two modes:
  search (weekly): Discover new Christian videos for each tracked search term.
                   Costs ~100 API units per search term. Run once per week.
  stats  (daily):  Poll view/like/comment counts for all active tracked videos.
                   Costs 1 unit per 50 videos. Very cheap.

Daily quota: 10,000 units. Weekly search of ~60 terms = 6,000 units.
Daily stats poll of 500 videos = 10 units.

Usage:
    python collectors/youtube.py --mode search   # weekly discovery
    python collectors/youtube.py --mode stats    # daily stats (default)
    python collectors/youtube.py                 # defaults to stats

GitHub Actions:
    collect-trends.yml  (weekly)  → calls --mode search
    collect-youtube.yml (daily)   → calls --mode stats (default)
"""

import re
import sys
import time
import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from db.client import get_client, upsert, log_run_start, log_run_finish

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MAX_RESULTS_PER_SEARCH = 25       # videos per search query (max 50)
VIDEO_MAX_AGE_DAYS     = 90       # retire videos older than this
STATS_BATCH_SIZE       = 50       # videos.list max IDs per call
PUBLISHED_WITHIN_DAYS  = 30       # only surface recently published videos


# ─── Client ───────────────────────────────────────────────────────────────────

def _yt():
    return build("youtube", "v3", developerKey=os.environ["YOUTUBE_API_KEY"])


# ─── Search mode (weekly) ─────────────────────────────────────────────────────

def _load_youtube_terms(client) -> list[dict]:
    return (
        client.table("search_terms")
        .select("id, term, category, source")
        .eq("is_active", True)
        .in_("source", ["youtube", "both"])
        .execute()
        .data
    )


def _search_videos(yt_client, term: str, published_after: str) -> list[dict]:
    """Run one YouTube search. Returns raw snippet items."""
    try:
        resp = (
            yt_client.search()
            .list(
                q=term,
                part="snippet",
                type="video",
                order="relevance",
                publishedAfter=published_after,
                relevanceLanguage="en",
                regionCode="US",
                maxResults=MAX_RESULTS_PER_SEARCH,
            )
            .execute()
        )
        return resp.get("items", [])
    except HttpError as e:
        log.warning("  Search failed for %r: %s", term, e)
        return []


def run_search(client, yt_client) -> int:
    """
    Weekly job: search YouTube for each active term, store new video IDs.
    Also retires videos older than VIDEO_MAX_AGE_DAYS.
    Returns count of rows written.
    """
    terms = _load_youtube_terms(client)
    log.info("Searching YouTube for %d terms...", len(terms))

    published_after = (
        datetime.now(timezone.utc) - timedelta(days=PUBLISHED_WITHIN_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    video_rows: list[dict] = []
    seen_ids: set[str] = set()

    for i, term in enumerate(terms):
        log.info("[%d/%d] %r", i + 1, len(terms), term["term"])
        items = _search_videos(yt_client, term["term"], published_after)
        log.info("  %d results", len(items))
        time.sleep(2)  # stay under YouTube's 100 searches/100s per-minute limit

        for item in items:
            vid_id = item["id"]["videoId"]
            if vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)

            s = item.get("snippet", {})
            video_rows.append({
                "video_id":       vid_id,
                "title":          (s.get("title") or "")[:500],
                "channel_id":     s.get("channelId", ""),
                "channel_name":   (s.get("channelTitle") or "")[:200],
                "published_at":   s.get("publishedAt"),
                "search_term_id": term["id"],
                "category":       term["category"],
                "is_active":      True,
            })

    # Retire old videos so we don't waste daily stats quota on stale content
    cutoff = (date.today() - timedelta(days=VIDEO_MAX_AGE_DAYS)).isoformat()
    log.info("Retiring videos published before %s...", cutoff)
    try:
        (
            client.table("youtube_videos")
            .update({"is_active": False})
            .lt("published_at", cutoff)
            .execute()
        )
    except Exception as e:
        log.warning("Could not retire old videos: %s", e)

    if not video_rows:
        log.info("No new videos to store.")
        return 0

    written = upsert("youtube_videos", video_rows, on_conflict="video_id")
    log.info("Upserted %d video rows.", written)
    return written


# ─── Stats mode (daily) ───────────────────────────────────────────────────────

def _load_active_video_ids(client) -> list[str]:
    rows = (
        client.table("youtube_videos")
        .select("video_id")
        .eq("is_active", True)
        .execute()
        .data
    )
    return [r["video_id"] for r in rows]


def _parse_duration(iso: str) -> int | None:
    """Parse ISO 8601 duration string to total seconds. e.g. 'PT1M30S' → 90."""
    if not iso:
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    return h * 3600 + mins * 60 + secs


def _fetch_stats_batch(yt_client, video_ids: list[str]) -> dict[str, dict]:
    """
    Fetch statistics + contentDetails for up to 50 video IDs (1 API unit).
    Returns {video_id: {view_count, like_count, comment_count, duration_seconds, is_short}}.
    """
    try:
        resp = (
            yt_client.videos()
            .list(part="statistics,contentDetails", id=",".join(video_ids))
            .execute()
        )
        result = {}
        for item in resp.get("items", []):
            stats   = item.get("statistics",     {})
            content = item.get("contentDetails", {})
            dur_sec = _parse_duration(content.get("duration", ""))
            result[item["id"]] = {
                "view_count":       int(stats.get("viewCount", 0)),
                "like_count":       int(stats["likeCount"])    if "likeCount"    in stats else None,
                "comment_count":    int(stats["commentCount"]) if "commentCount" in stats else None,
                "duration_seconds": dur_sec,
                "is_short":         (dur_sec is not None and dur_sec <= 60),
            }
        return result
    except HttpError as e:
        log.warning("  Stats batch failed: %s", e)
        return {}


def run_stats(client, yt_client) -> int:
    """
    Daily job: poll current stats for all active tracked videos.
    Returns count of rows written.
    """
    video_ids = _load_active_video_ids(client)
    if not video_ids:
        log.info("No active videos to poll.")
        return 0

    log.info("Polling stats for %d active videos...", len(video_ids))
    today = date.today().isoformat()
    snapshot_rows: list[dict] = []
    video_meta_rows: list[dict] = []     # duration + is_short for youtube_videos

    for i in range(0, len(video_ids), STATS_BATCH_SIZE):
        batch = video_ids[i : i + STATS_BATCH_SIZE]
        stats_map = _fetch_stats_batch(yt_client, batch)
        for vid_id, stats in stats_map.items():
            # Engagement snapshot (youtube_snapshots table)
            snapshot_rows.append({
                "video_id":      vid_id,
                "snapshot_date": today,
                "view_count":    stats["view_count"],
                "like_count":    stats["like_count"],
                "comment_count": stats["comment_count"],
            })
            # Duration metadata (youtube_videos table)
            if stats.get("duration_seconds") is not None:
                video_meta_rows.append({
                    "video_id":         vid_id,
                    "is_short":         stats["is_short"],
                    "duration_seconds": stats["duration_seconds"],
                })
        log.info(
            "  Batch %d-%d: %d stats returned",
            i + 1, min(i + STATS_BATCH_SIZE, len(video_ids)), len(stats_map)
        )

    if not snapshot_rows:
        log.info("No stats returned.")
        return 0

    written = upsert(
        "youtube_snapshots",
        snapshot_rows,
        on_conflict="video_id,snapshot_date",
    )
    log.info("Wrote %d stat snapshots.", written)

    # Back-fill is_short / duration_seconds onto youtube_videos
    if video_meta_rows:
        upsert("youtube_videos", video_meta_rows, on_conflict="video_id")
        log.info("Updated duration/is_short for %d videos.", len(video_meta_rows))

    return written


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YouTube data collector")
    parser.add_argument(
        "--mode",
        choices=["search", "stats"],
        default="stats",
        help="search = weekly video discovery; stats = daily stats poll (default)",
    )
    args = parser.parse_args()

    client = get_client()
    run_id = log_run_start("youtube")

    try:
        yt_client = _yt()

        if args.mode == "search":
            log.info("=== YouTube search mode (weekly) ===")
            written = run_search(client, yt_client)
        else:
            log.info("=== YouTube stats mode (daily) ===")
            written = run_stats(client, yt_client)

        log.info("Done. %d rows written.", written)
        log_run_finish(run_id, "success", written)

    except Exception as e:
        log.error("Fatal error: %s", e, exc_info=True)
        log_run_finish(run_id, "failed", errors=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
