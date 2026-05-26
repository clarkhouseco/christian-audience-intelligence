"""
One-time seed script.
Populates the `regions` and `search_terms` tables from config YAML files.
Safe to re-run — uses upsert so duplicates are skipped or updated.

Usage:
    python scripts/seed.py
"""

import sys
import os
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from db.client import get_client, upsert


CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_yaml(filename: str) -> dict:
    with open(CONFIG_DIR / filename, encoding="utf-8") as f:
        return yaml.safe_load(f)


def seed_regions(data: dict) -> int:
    rows = []
    seen_codes = set()

    for state in data.get("states", []):
        if state["code"] not in seen_codes:
            rows.append({"type": "state", "code": state["code"], "name": state["name"]})
            seen_codes.add(state["code"])

    for dma in data.get("dmas", []):
        if dma["code"] not in seen_codes:
            rows.append({"type": "dma", "code": dma["code"], "name": dma["name"]})
            seen_codes.add(dma["code"])

    written = upsert("regions", rows, on_conflict="code")
    print(f"  Regions: {written} rows upserted ({len(rows)} total in config)")
    return written


def seed_search_terms(data: dict) -> int:
    rows = []

    # Anchor term
    for anchor in data.get("anchor_terms", []):
        rows.append({
            "term": anchor["term"],
            "category": anchor["category"],
            "source": anchor["source"],
            "is_anchor": True,
            "is_seasonal": False,
            "is_active": True,
        })

    seasonal_set = {s.lower() for s in data.get("seasonal_exclusions", [])}

    for category, terms in data.get("categories", {}).items():
        for entry in terms:
            rows.append({
                "term": entry["term"],
                "category": category,
                "source": entry["source"],
                "is_anchor": False,
                "is_seasonal": entry["term"].lower() in seasonal_set,
                "is_active": True,
            })

    written = upsert("search_terms", rows, on_conflict="term")
    print(f"  Search terms: {written} rows upserted ({len(rows)} total in config)")
    return written


def main():
    print("Loading config files...")
    regions_data = load_yaml("regions.yaml")
    terms_data = load_yaml("search_terms.yaml")

    print("\nSeeding regions...")
    seed_regions(regions_data)

    print("\nSeeding search terms...")
    seed_search_terms(terms_data)

    print("\nSeed complete.")


if __name__ == "__main__":
    main()
