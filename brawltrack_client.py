"""Small, defensive client for BrawlTrack's documented public API.

BrawlTrack augments the official Supercell catalog with live meta/assets.
Only documented endpoints live here; site-only pages stay in the existing
web/meta parser until BrawlTrack exposes them in OpenAPI.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

BASE_URL = os.getenv("BRAWLTRACK_API_BASE", "https://brawltrack.app/api").rstrip("/")
TIMEOUT = float(os.getenv("BRAWLTRACK_TIMEOUT", "12"))


class BrawlTrackError(RuntimeError):
    pass


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{BASE_URL}/{path.lstrip('/')}"
    try:
        response = requests.get(
            url,
            params=params,
            timeout=TIMEOUT,
            headers={"Accept": "application/json", "User-Agent": "SensGPT-TitaniAbusivi/1.0"},
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BrawlTrackError(f"BrawlTrack request failed: {url}: {exc}") from exc


def status() -> Any:
    return _get("status")


def brawlers() -> Any:
    """Documented BrawlTrack brawler catalog: statistics, assets, metadata."""
    return _get("brawlers")


def player(tag: str) -> Any:
    clean = str(tag or "").strip().lstrip("#").upper()
    if not clean:
        raise ValueError("player tag is required")
    return _get(f"player/{quote(clean, safe='')}")


def search_players(query: str) -> Any:
    clean = str(query or "").strip()
    if not clean:
        raise ValueError("search query is required")
    return _get("search/players", params={"q": clean})


def normalize_brawler_catalog(payload: Any) -> Dict[int, Dict[str, Any]]:
    """Return {Supercell brawler id: record} without guessing IDs from names."""
    if isinstance(payload, dict):
        rows = payload.get("brawlers") or payload.get("items") or payload.get("data") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    result: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id") or row.get("brawlerId") or row.get("brawler_id")
        try:
            brawler_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        result[brawler_id] = row
    return result
