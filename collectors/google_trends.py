"""
Google Trends collector.

For each active search term, pulls:
  1. National weekly timeseries (last ~13 weeks) -> trends_snapshots
  2. State-level interest snapshot               -> trends_snapshots
  3. Rising related queries                      -> rising_queries

pytrends limits batches to 5 keywords. We use "bible" as an anchor term
in every batch so week-over-week change per term is the meaningful signal.

Run weekly via GitHub Actions (collect-trends.yml) or manually:
    python collectors/google_trends.py
"""

import sys
import time
import random
import logging
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pytrends.request import TrendReq
from db.client import get_client, upsert, insert, log_run_start, log_run_finish

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BATCH_SIZE = 4           # terms per batch (+ anchor = 5, Google's hard limit)
SLEEP_MIN = 10           # seconds between API calls — stay under rate limit
SLEEP_MAX = 20
TIMEFRAME = "today 3-m"  # ~13 weeks of weekly data
UPSERT_CHUNK = 400       # rows per Supabase write

# Google Trends returns state names that don't always match our regions table.
# Map any mismatches here.
STATE_NAME_FIXES = {
    "District of Columbia": "Washington D.C.",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _sleep():
    t = random.uniform(SLEEP_MIN, SLEEP_MAX)
    log.info("  sleeping %.1fs", t)
    time.sleep(t)


def _chunked_upsert(table: str, rows: list[dict], on_conflict: str) -> int:
    total = 0
    for i in range(0, len(rows), UPSERT_CHUNK):
        total += upsert(table, rows[i:i + UPSERT_CHUNK], on_conflict=on_conflict)
    return total


# ─── Supabase loading ─────────────────────────────────────────────────────────

def _load_terms(client):
    rows = (
        client.table("search_terms")
        .select("id, term, category, source, is_anchor")
        .eq("is_active", True)
        .execute()
        .data
    )
    anchor = next((r for r in rows if r["is_anchor"]), None)
    terms = [
        r for r in rows
        if not r["is_anchor"] and r["source"] in ("google_trends", "both")
    ]
    return anchor, terms


def _load_region_maps(client):
    rows = client.table("regions").select("id, code, name").execute().data
    by_name = {r["name"]: r["id"] for r in rows}
    by_code = {r["code"]: r["id"] for r in rows}
    return by_name, by_code


# ─── pytrends calls ───────────────────────────────────────────────────────────

def _national_timeseries(pt: TrendReq, kw_list: list[str]) -> dict[str, dict]:
    """Pull weekly national interest. Returns {term: {week_str: index}}."""
    df = pt.interest_over_time()
    if df.empty:
        return {}
    result = {}
    for term in kw_list:
        if term not in df.columns:
            continue
        result[term] = {
            str(ts.date()): int(val)
            for ts, val in df[term].items()
        }
    return result


def _state_interest(pt: TrendReq, kw_list: list[str]) -> dict[str, dict]:
    """Pull state-level interest snapshot. Returns {term: {state_name: index}}."""
    df = pt.interest_by_region(resolution="REGION", inc_low_vol=True)
    if df.empty:
        return {}
    result = {}
    for term in kw_list:
        if term not in df.columns:
            continue
        result[term] = {
            name: int(val)
            for name, val in df[term].items()
            if int(val) > 0
        }
    return result


def _rising_queries(pt: TrendReq, kw_list: list[str], anchor_term: str) -> list[dict]:
    """Pull rising related queries. Returns [{query, source_term, rise_value}]."""
    related = pt.related_queries()
    rows = []
    for term, data in related.items():
        if term == anchor_term or not data:
            continue
        rising = data.get("rising")
        if rising is None or rising.empty:
            continue
        for _, row in rising.head(5).iterrows():
            rows.append({
                "query": str(row["query"]),
                "source_term": term,
                "rise_value": str(row["value"]),
            })
    return rows


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    client = get_client()
    run_id = log_run_start("google_trends")

    try:
        log.info("Loading terms and regions from Supabase...")
        anchor, terms = _load_terms(client)
        if not anchor:
            raise ValueError("No anchor term found — run scripts/seed.py first")

        name_to_id, code_to_id = _load_region_maps(client)
        national_id = code_to_id["US"]

        # Apply known name fixes to the lookup map
        for google_name, our_name in STATE_NAME_FIXES.items():
            if our_name in name_to_id:
                name_to_id[google_name] = name_to_id[our_name]

        term_to_id = {t["term"]: t["id"] for t in terms}
        term_to_id[anchor["term"]] = anchor["id"]

        log.info("  %d active terms across %d regions", len(terms), len(name_to_id))

        batches = [
            [anchor["term"]] + [t["term"] for t in terms[i:i + BATCH_SIZE]]
            for i in range(0, len(terms), BATCH_SIZE)
        ]
        log.info("  %d batches of up to %d terms each", len(batches), BATCH_SIZE)

        pt = TrendReq(hl="en-US", tz=360)
        current_week = str(date.today())

        snapshot_rows: list[dict] = []
        rising_rows: list[dict] = []
        errors: list[str] = []

        for i, kw_list in enumerate(batches):
            log.info("─── Batch %d/%d: %s", i + 1, len(batches), kw_list[1:])

            # Build payload once — all three calls below reuse it
            pt.build_payload(kw_list, timeframe=TIMEFRAME, geo="US")

            # 1. National weekly timeseries
            try:
                ts_data = _national_timeseries(pt, kw_list)
                for term, weeks in ts_data.items():
                    tid = term_to_id.get(term)
                    if tid is None:
                        continue
                    for week_str, idx in weeks.items():
                        snapshot_rows.append({
                            "term_id": tid,
                            "region_id": national_id,
                            "week_start": week_str,
                            "interest_index": idx,
                        })
                log.info("  national: %d datapoints", sum(len(v) for v in ts_data.values()))
            except Exception as e:
                log.warning("  national timeseries failed: %s", e)
                errors.append(f"batch {i+1} national: {e}")

            _sleep()

            # 2. State-level snapshot
            try:
                state_data = _state_interest(pt, kw_list)
                for term, state_map in state_data.items():
                    tid = term_to_id.get(term)
                    if tid is None:
                        continue
                    for state_name, idx in state_map.items():
                        rid = name_to_id.get(state_name)
                        if rid is None:
                            log.debug("  unknown state name from Google: %r", state_name)
                            continue
                        snapshot_rows.append({
                            "term_id": tid,
                            "region_id": rid,
                            "week_start": current_week,
                            "interest_index": idx,
                        })
                log.info("  states: %d datapoints", sum(len(v) for v in state_data.values()))
            except Exception as e:
                log.warning("  state interest failed: %s", e)
                errors.append(f"batch {i+1} states: {e}")

            _sleep()

            # 3. Rising related queries
            try:
                rqs = _rising_queries(pt, kw_list, anchor["term"])
                for row in rqs:
                    src_id = term_to_id.get(row["source_term"])
                    rising_rows.append({
                        "query": row["query"],
                        "source_term_id": src_id,
                        "region_id": national_id,
                        "rise_value": row["rise_value"],
                        "week_of": current_week,
                    })
                log.info("  rising queries: %d", len(rqs))
            except Exception as e:
                log.warning("  rising queries failed: %s", e)
                errors.append(f"batch {i+1} rising: {e}")

            _sleep()

        # ── Deduplicate snapshot rows (anchor term appears in every batch) ────
        seen = {}
        for row in snapshot_rows:
            key = (row["term_id"], row["region_id"], row["week_start"])
            seen[key] = row  # last write wins
        snapshot_rows = list(seen.values())

        # ── Write everything to Supabase ──────────────────────────────────────
        log.info("Writing %d snapshot rows to Supabase...", len(snapshot_rows))
        written = _chunked_upsert(
            "trends_snapshots",
            snapshot_rows,
            on_conflict="term_id,region_id,week_start",
        )

        log.info("Writing %d rising query rows...", len(rising_rows))
        written += insert("rising_queries", rising_rows, on_conflict="query,region_id,week_of")

        status = "partial" if errors else "success"
        error_str = "\n".join(errors) if errors else None
        log.info("Done. %d rows written. Status: %s", written, status)
        if errors:
            log.warning("Errors encountered:\n%s", error_str)

        log_run_finish(run_id, status, written, errors=error_str)

    except Exception as e:
        log.error("Fatal error: %s", e, exc_info=True)
        log_run_finish(run_id, "failed", errors=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
