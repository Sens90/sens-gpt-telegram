import os
import re
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


def install_club_ranking_router():
    """Intercepta le classifiche club semplici prima che finiscano alla AI generativa."""
    import community_features

    current = community_features.CommunityFeatures.handle_command
    if getattr(current, "_sens_club_ranking_router", False):
        return

    aliases = {
        "titani": "TITANI ABUSIVI",
        "titani abusivi": "TITANI ABUSIVI",
        "tamarri": "TAMARRI ABUSIVI",
        "tamarri abusivi": "TAMARRI ABUSIVI",
        "tornadi": "TORNADI ABUSIVI",
        "tornadi abusivi": "TORNADI ABUSIVI",
        "talenti": "TALENTI ABUSIVI",
        "talenti abusivi": "TALENTI ABUSIVI",
    }

    async def routed(self, message, context, question):
        q = re.sub(r"^[!/]+", "", str(question or "").strip()).strip().strip("\"'“”‘’ ").strip()
        match = re.fullmatch(
            r"classific(?:a|he)\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)",
            q,
            re.I,
        )
        if match:
            club_name = aliases[match.group(1).casefold()]
            await message.reply_text(self.stat_ranking_text(message.chat_id, "trofei", club_name))
            print("CLASSIFICA CLUB DIRETTA:", club_name, flush=True)
            return True
        return await current(self, message, context, question)

    routed._sens_club_ranking_router = True
    community_features.CommunityFeatures.handle_command = routed
    print("CLASSIFICA CLUB ROUTER INSTALLATO", flush=True)


def sync_official_brawlers(timeout=30):
    """Sincronizza nel catalogo solo i dati restituiti dall'API ufficiale Supercell."""
    supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not supabase_url:
        raise RuntimeError("SUPABASE_URL non configurata")

    # Il router e indipendente dalla riuscita della sincronizzazione catalogo.
    install_club_ranking_router()

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
            "source_payload": item,
            "source_updated_at": now,
            "updated_at": now,
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
        "dataset": "brawlers",
        "source": "supercell_official",
        "last_success_at": now,
        "last_attempt_at": now,
        "last_status": "ok",
        "records_seen": len(rows),
        "details": {"via": "brawl-proxy", "official": True},
        "updated_at": now,
    }
    r = requests.post(
        f"{supabase_url}/rest/v1/content_sync_state?on_conflict=dataset,source",
        headers=_headers(),
        json=state,
        timeout=timeout,
    )
    r.raise_for_status()
    print(f"CATALOGO BRAWLER SUPERCELL: {len(rows)} sincronizzati", flush=True)
    return len(rows)


if __name__ == "__main__":
    sync_official_brawlers()
