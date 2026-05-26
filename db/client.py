"""
Supabase client wrapper.
All database access goes through this module so the rest of the codebase
never imports supabase directly.
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
    return _client


def upsert(table: str, rows: list[dict], on_conflict: str) -> int:
    """Insert or update rows. Returns count of rows written."""
    if not rows:
        return 0
    client = get_client()
    result = (
        client.table(table)
        .upsert(rows, on_conflict=on_conflict)
        .execute()
    )
    return len(result.data) if result.data else 0


def insert(table: str, rows: list[dict], on_conflict: str | None = None) -> int:
    """Insert rows. If on_conflict key provided, upserts (ignores duplicates)."""
    if not rows:
        return 0
    if on_conflict:
        return upsert(table, rows, on_conflict=on_conflict)
    client = get_client()
    result = client.table(table).insert(rows).execute()
    return len(result.data) if result.data else 0


def select(table: str, columns: str = "*", **filters) -> list[dict]:
    """Simple select with optional equality filters."""
    client = get_client()
    query = client.table(table).select(columns)
    for col, val in filters.items():
        query = query.eq(col, val)
    return query.execute().data or []


def log_run_start(source: str) -> int:
    """Insert a collection_runs row, return the run ID."""
    client = get_client()
    result = (
        client.table("collection_runs")
        .insert({"source": source, "status": "running"})
        .execute()
    )
    return result.data[0]["id"]


def log_run_finish(run_id: int, status: str, rows_written: int = 0, errors: str = None, notes: str = None):
    client = get_client()
    from datetime import datetime, timezone
    (
        client.table("collection_runs")
        .update({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "rows_written": rows_written,
            "errors": errors,
            "notes": notes,
        })
        .eq("id", run_id)
        .execute()
    )
