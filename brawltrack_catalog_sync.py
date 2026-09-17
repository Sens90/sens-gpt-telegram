"""Enrich the official Supercell brawler catalog with BrawlTrack data.

Safety rules:
- Supercell IDs remain authoritative.
- Existing Italian localization is never overwritten.
- Only fields that already exist in brawlers_catalog are updated.
- The complete BrawlTrack row is retained in source_payload for future meta/assets use.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import requests

from brawltrack_client import BrawlTrackError, brawlers, normalize_brawler_catalog

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")
TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))


def _headers(extra=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _rest(method, path, **kwargs):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Supabase configuration missing")
    response = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{path.lstrip('/')}",
        headers=_headers(kwargs.pop("headers", None)),
        timeout=TIMEOUT,
        **kwargs,
    )
    response.raise_for_status()
    if not response.content:
        return None
    return response.json()


def _catalog_columns():
    # PostgREST cannot introspect information_schema reliably, so read one row and
    # restrict writes to keys already exposed by the table.
    rows = _rest("GET", "brawlers_catalog?select=*&limit=1") or []
    return set(rows[0].keys()) if rows else {
        "brawler_id", "name_en", "name_it", "rarity", "image_url",
        "source", "source_payload", "source_updated_at", "updated_at",
    }


def _first(row, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def _asset_url(row):
    direct = _first(row, "imageUrl", "image_url", "image", "iconUrl", "icon_url")
    if isinstance(direct, str):
        return direct
    assets = row.get("assets")
    if isinstance(assets, dict):
        for key in ("image", "icon", "portrait", "avatar"):
            value = assets.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                nested = _first(value, "url", "src")
                if isinstance(nested, str):
                    return nested
    return None


def sync_brawltrack_catalog():
    now = datetime.now(timezone.utc).isoformat()
    payload = brawlers()
    remote = normalize_brawler_catalog(payload)
    columns = _catalog_columns()
    current = _rest("GET", "brawlers_catalog?select=brawler_id,name_it") or []
    known_ids = {int(r["brawler_id"]): r for r in current if r.get("brawler_id") is not None}

    updated = 0
    skipped_unknown_id = 0
    for brawler_id, row in remote.items():
        # Never let a third-party catalog create/rename the authoritative Supercell roster.
        if brawler_id not in known_ids:
            skipped_unknown_id += 1
            continue
        patch = {
            "source_payload": {"supercell_primary": True, "brawltrack": row},
            "source_updated_at": now,
            "updated_at": now,
        }
        image = _asset_url(row)
        rarity = _first(row, "rarity", "rarityName", "rarity_name")
        if image:
            patch["image_url"] = image
        if rarity:
            patch["rarity"] = rarity if isinstance(rarity, str) else json.dumps(rarity, ensure_ascii=False)
        patch = {k: v for k, v in patch.items() if k in columns}
        if not patch:
            continue
        _rest(
            "PATCH",
            f"brawlers_catalog?brawler_id=eq.{brawler_id}",
            data=json.dumps(patch, ensure_ascii=False).encode("utf-8"),
            headers={"Prefer": "return=minimal"},
        )
        updated += 1

    return {
        "source": "brawltrack",
        "remote_brawlers": len(remote),
        "updated": updated,
        "skipped_unknown_supercell_id": skipped_unknown_id,
        "italian_names_overwritten": 0,
        "synced_at": now,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(sync_brawltrack_catalog(), ensure_ascii=False))
    except (BrawlTrackError, requests.RequestException, RuntimeError) as exc:
        raise SystemExit(f"BrawlTrack sync failed: {exc}")
