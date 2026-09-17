"""Cache documented BrawlTrack brawler/meta payloads in Supabase.

Supercell IDs remain authoritative. Italian localization is intentionally
kept in the existing catalog tables and is never overwritten here.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

import requests

from brawltrack_client import brawlers, normalize_brawler_catalog

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", "")
TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))


def _num(row: Dict[str, Any], *keys: str):
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip().rstrip("%").replace(",", ".")
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _first(row: Dict[str, Any], *keys: str):
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _headers():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL/service role key missing")
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _known_ids() -> set[int]:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/brawlers_catalog",
        params={"select": "brawler_id"}, headers=_headers(), timeout=TIMEOUT,
    )
    r.raise_for_status()
    return {int(x["brawler_id"]) for x in r.json()}


def _build_row(brawler_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    merged = {**row, **stats}
    builds = _first(merged, "popularBuilds", "popular_builds", "builds", "loadouts") or []
    modes = _first(merged, "modes", "gameModes", "game_modes", "modeStats") or {}
    return {
        "brawler_id": brawler_id,
        "brawler_name": _first(row, "name", "brawlerName", "brawler_name"),
        "win_rate": _num(merged, "winRate", "win_rate", "winrate"),
        "pick_rate": _num(merged, "pickRate", "pick_rate", "usageRate", "usage_rate"),
        "star_rate": _num(merged, "starRate", "star_rate", "starPlayerRate", "star_player_rate"),
        "rank_label": _first(merged, "rank", "tier", "rankLabel", "rank_label"),
        "popular_builds": builds if isinstance(builds, (list, dict)) else [],
        "modes": modes if isinstance(modes, (list, dict)) else {},
        "source_url": f"https://brawltrack.app/brawlers/{brawler_id}",
        "source_payload": row,
        "source_updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def sync() -> Dict[str, int]:
    catalog = normalize_brawler_catalog(brawlers())
    known = _known_ids()
    rows = [_build_row(i, row) for i, row in catalog.items() if i in known]
    if rows:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/brawltrack_meta_cache?on_conflict=brawler_id",
            headers=_headers(), data=json.dumps(rows, ensure_ascii=False), timeout=TIMEOUT,
        )
        r.raise_for_status()
    return {"seen": len(catalog), "known": len(known), "cached": len(rows)}


if __name__ == "__main__":
    print(json.dumps(sync(), ensure_ascii=False))
