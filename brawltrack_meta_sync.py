"""Cache BrawlTrack API data and enrich it from public brawler pages.

Supercell IDs remain authoritative. Italian localization stays in the existing
catalog tables and is never overwritten here. Page enrichment is deliberately
separate from the documented API payload and records its provenance.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict

import requests
from bs4 import BeautifulSoup

from brawltrack_client import brawlers, normalize_brawler_catalog

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", "")
TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))
PAGE_TIMEOUT = float(os.getenv("BRAWLTRACK_PAGE_TIMEOUT", "15"))
PAGE_DELAY = max(0.0, float(os.getenv("BRAWLTRACK_PAGE_DELAY", "0.12")))
UA = "SensGPT-TitaniAbusivi/1.0"


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
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}


def _known_ids() -> set[int]:
    r = requests.get(f"{SUPABASE_URL}/rest/v1/brawlers_catalog",
                     params={"select": "brawler_id"}, headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return {int(x["brawler_id"]) for x in r.json()}


def _section(text: str, start: str, ends: tuple[str, ...]) -> str:
    pos = text.find(start)
    if pos < 0:
        return ""
    pos += len(start)
    end_positions = [text.find(e, pos) for e in ends]
    end_positions = [p for p in end_positions if p >= 0]
    end = min(end_positions) if end_positions else len(text)
    return text[pos:end].strip()


def _page_enrichment(brawler_id: int) -> Dict[str, Any]:
    url = f"https://brawltrack.app/brawlers/{brawler_id}"
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=PAGE_TIMEOUT)
    r.raise_for_status()
    text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)

    def pct(label: str):
        m = re.search(re.escape(label) + r"\s+(\d+(?:[.,]\d+)?)%", text, re.I)
        return float(m.group(1).replace(",", ".")) if m else None

    build_text = _section(text, "Popular Builds", ("Best Teammates", "Star Powers", "Gadgets", "Best Game Modes"))
    modes_text = _section(text, "Best Game Modes", ("Best Maps", "Meta Performance Check", "Trivia & Mechanics"))
    maps_text = _section(text, "Best Maps", ("Meta Performance Check", "Trivia & Mechanics"))

    builds = {"source": "brawltrack_public_page", "raw_text": build_text[:8000]} if build_text else []
    modes = {"source": "brawltrack_public_page", "raw_text": modes_text[:12000],
             "best_maps_raw_text": maps_text[:12000]} if (modes_text or maps_text) else {}
    return {"win_rate": pct("Win Rate"), "pick_rate": pct("Meta Usage"),
            "star_rate": pct("Star Rate"), "popular_builds": builds, "modes": modes,
            "page_url": url}


def _build_row(brawler_id: int, row: Dict[str, Any], enrich: Dict[str, Any] | None = None) -> Dict[str, Any]:
    stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    merged = {**row, **stats}; enrich = enrich or {}
    builds = enrich.get("popular_builds") or _first(merged, "popularBuilds", "popular_builds", "builds", "loadouts") or []
    modes = enrich.get("modes") or _first(merged, "modes", "gameModes", "game_modes", "modeStats") or {}
    now = datetime.now(timezone.utc).isoformat()
    payload = dict(row)
    if enrich:
        payload["public_page_enrichment"] = {k: v for k, v in enrich.items() if k != "page_url"}
    return {
        "brawler_id": brawler_id,
        "brawler_name": _first(row, "name", "brawlerName", "brawler_name"),
        "win_rate": enrich.get("win_rate") if enrich.get("win_rate") is not None else _num(merged, "winRate", "win_rate", "winrate"),
        "pick_rate": enrich.get("pick_rate") if enrich.get("pick_rate") is not None else _num(merged, "pickRate", "pick_rate", "usageRate", "usage_rate", "metaUsage", "meta_usage", "usage"),
        "star_rate": enrich.get("star_rate") if enrich.get("star_rate") is not None else _num(merged, "starRate", "star_rate", "starPlayerRate", "star_player_rate", "mvpRate", "mvp_rate"),
        "rank_label": _first(merged, "rank", "tier", "rankLabel", "rank_label"),
        "popular_builds": builds if isinstance(builds, (list, dict)) else [],
        "modes": modes if isinstance(modes, (list, dict)) else {},
        "source_url": enrich.get("page_url") or f"https://brawltrack.app/brawlers/{brawler_id}",
        "source_payload": payload, "source_updated_at": now, "updated_at": now,
    }


def sync() -> Dict[str, int]:
    catalog = normalize_brawler_catalog(brawlers()); known = _known_ids(); rows = []; enriched = 0; page_errors = 0
    for brawler_id, row in catalog.items():
        if brawler_id not in known:
            continue
        extra = {}
        try:
            extra = _page_enrichment(brawler_id); enriched += 1
        except Exception as exc:
            page_errors += 1
            print("BRAWLTRACK PAGE ENRICH ERROR:", brawler_id, repr(exc), flush=True)
        rows.append(_build_row(brawler_id, row, extra))
        if PAGE_DELAY:
            time.sleep(PAGE_DELAY)
    if rows:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/brawltrack_meta_cache?on_conflict=brawler_id",
                          headers=_headers(), data=json.dumps(rows, ensure_ascii=False), timeout=max(TIMEOUT, 30))
        r.raise_for_status()
    return {"seen": len(catalog), "known": len(known), "cached": len(rows), "enriched": enriched, "page_errors": page_errors}


if __name__ == "__main__":
    print(json.dumps(sync(), ensure_ascii=False))
