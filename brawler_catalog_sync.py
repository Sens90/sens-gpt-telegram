import os
from datetime import datetime, timezone

import requests


def _headers():
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY non configurata")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _proxy_brawlers(timeout=30):
    proxy_url = os.environ.get("BRAWL_OFFICIAL_PROXY_URL")
    proxy_key = os.environ.get("BRAWL_OFFICIAL_PROXY_KEY")
    if not proxy_url or not proxy_key:
        raise RuntimeError("Proxy ufficiale Supercell non configurato")
    r = requests.get(
        proxy_url,
        params={"action": "brawlers"},
        headers={
            "X-Sens-Key": proxy_key,
            "Accept": "application/json",
            "User-Agent": "SensGPT-TitaniAbusivi/1.0",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json() or {}
    return data.get("items") or data.get("list") or []


def sync_official_brawlers(timeout=30):
    """Sincronizza nel catalogo solo i dati restituiti dall'API ufficiale Supercell."""
    supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not supabase_url:
        raise RuntimeError("SUPABASE_URL non configurata")

    items = _proxy_brawlers(timeout=timeout)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in items:
        brawler_id = item.get("id")
        name = item.get("name")
        if brawler_id is None or not name:
            continue
        rows.append({
            "brawler_id": int(brawler_id),
            "name_en": str(name),
            "source": "supercell_official",
            "source_updated_at": now,
            "raw_data": item,
        })

    if rows:
        r = requests.post(
            f"{supabase_url}/rest/v1/brawlers_catalog?on_conflict=brawler_id",
            headers=_headers(),
            json=rows,
            timeout=timeout,
        )
        r.raise_for_status()

    state = {
        "content_type": "brawlers",
        "source": "supercell_official",
        "last_synced_at": now,
        "records_synced": len(rows),
        "status": "ok",
    }
    r = requests.post(
        f"{supabase_url}/rest/v1/content_sync_state?on_conflict=content_type,source",
        headers=_headers(),
        json=state,
        timeout=timeout,
    )
    r.raise_for_status()
    print(f"CATALOGO BRAWLER SUPERCELL: {len(rows)} sincronizzati", flush=True)
    return len(rows)


if __name__ == "__main__":
    sync_official_brawlers()
