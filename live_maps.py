"""Read the public data used by Brawl Planet's active-map component.

Rotation is time-bounded, never inferred from the map catalogue. Tables are
rendered directly, so missing data cannot invalidate unrelated maps.
"""
import csv
import gzip
import io
import json
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE = "https://storage.googleapis.com/brawlanalyzer-public/"
ROME = ZoneInfo("Europe/Rome")
LOG = logging.getLogger(__name__)
_CACHE = {}
SECTIONS = {
    "individual": "Individuali", "teams": "Squadre",
    "solo": "Solo — individuali", "duo_individual": "Duo — individuali",
    "duo_team": "Duo — squadre", "trio_individual": "Trio — individuali",
    "trio_team": "Trio — squadre",
}
METRICS = {
    "wr": ("Vittorie", "%"), "win_rate": ("Vittorie", "%"),
    "ur": ("Scelta", "%"), "use_rate": ("Scelta", "%"),
    "sr": ("Star Player", "%"), "starplayer_rate": ("Star Player", "%"),
    "avg_rank": ("Piazzamento medio", ""), "tm": ("Partite", ""),
}
MODES = {
    "brawlBall": "Brawl Ball", "gemGrab": "Gem Grab", "hotZone": "Hot Zone",
    "bounty": "Bounty", "heist": "Heist", "knockout": "Knockout",
    "showdown": "Showdown", "airHockey": "Brawl Hockey",
    "brawlArena": "Brawl Arena", "deathmatch5v5": "Wipeout 5v5",
    "wipeout": "Wipeout", "basketBrawl": "Basket Brawl", "payload": "Payload",
}


def get_json(path, ttl=300):
    cached = _CACHE.get(path)
    if cached and time.monotonic() - cached[0] < ttl:
        return cached[1]
    url = path if path.startswith("https://") else BASE + path
    with urlopen(Request(url, headers={"User-Agent": "SensGPT/1.0"}), timeout=30) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Source response too large")
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    result = json.loads(raw)
    _CACHE[path] = (time.monotonic(), result)
    return result


def safe_get(path, ttl=300):
    try:
        return get_json(path, 60 if path == "event_rotation.json.gz" else ttl)
    except Exception as error:
        LOG.warning("LIVE_MAPS source unavailable path=%s error=%s", path, type(error).__name__)
        return None


def event_time(value):
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def active_events(rows, now):
    active = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        start, end = event_time(row.get("start_time")), event_time(row.get("end_time"))
        key, mode = row.get("event_map_id", ""), row.get("event_mode", "")
        if (not start or not end or not start <= now < end
                or not re.fullmatch(r"[a-z0-9_]+", key)
                or not re.fullmatch(r"[A-Za-z0-9]+", mode)):
            continue
        if key not in active:
            active[key] = dict(row)
        elif end < event_time(active[key]["end_time"]):
            active[key]["end_time"] = row["end_time"]
    return sorted(active.values(), key=lambda row: (row["event_mode"], row["event_map_id"]))


def localized(names, category, value):
    if str(value).upper() in {"MR. P", "MISTER P"}:
        return "Mr. P"
    return names.get(category, {}).get(str(value).upper(), str(value))


def valid_rows(rows):
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        team = row.get("team")
        if team is not None:
            if (not isinstance(team, list) or len(team) < 2
                    or not all(isinstance(v, str) and v for v in team)
                    or len(set(team)) != len(team)):
                continue
        elif not isinstance(row.get("brawler", row.get("brawler_name")), str):
            continue
        clean = {k: v for k, v in row.items() if k in ("brawler", "brawler_name", "team")}
        for key, value in row.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                # Pick rates in this source can exceed 100 (multiple selections).
                if key in ("wr", "win_rate", "sr", "starplayer_rate") and value > 100:
                    continue
                clean[key] = value
        if any(k in clean for k in ("wr", "win_rate", "avg_rank")):
            result.append(clean)
    return result


def row_text(row, names):
    identity = row.get("team") or [row.get("brawler", row.get("brawler_name"))]
    label = ", ".join(localized(names, "brawlers", name) for name in identity)
    metrics = []
    for key, value in row.items():
        if key in ("team", "brawler", "brawler_name"):
            continue
        title, suffix = METRICS.get(key, (key, ""))
        number = f"{value:g}" if key == "tm" else f"{value:.2f}".rstrip("0").rstrip(".")
        metrics.append(f"{title} {number.replace('.', ',')}{suffix}")
    return label + " — " + " · ".join(metrics)


def collect_report(dataset="both", now=None, fetch=safe_get, secondary=None):
    now = now or datetime.now(timezone.utc)
    # A failure in one fetch never discards independent successful responses.
    paths = ["event_rotation.json.gz", "i18n/names.it.json.gz"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        initial = dict(zip(paths, pool.map(fetch, paths)))
    events = active_events(initial[paths[0]], now)
    names = initial[paths[1]] if isinstance(initial[paths[1]], dict) else {}
    if not events:
        return {"now": now, "events": [], "names": names, "maps": [], "rotation_missing": True}
    paths = [f"normal-results/{mode}.json.gz" for mode in sorted({e["event_mode"] for e in events})]
    if dataset != "ladder":
        paths.append("pl-results.json.gz")
    with ThreadPoolExecutor(max_workers=6) as pool:
        data = dict(zip(paths, pool.map(fetch, paths)))
    data = {key: value if isinstance(value, dict) else {} for key, value in data.items()}
    maps = []
    for event in events:
        key = event["event_map_id"]
        normal = (data.get(f"normal-results/{event['event_mode']}.json.gz") or {}).get(key, {})
        ranked = (data.get("pl-results.json.gz") or {}).get(key, {})
        normal = normal if isinstance(normal, dict) else {}
        ranked = ranked if isinstance(ranked, dict) else {}
        entry = {"event": event, "datasets": [], "secondary": None}
        for label, raw in (("Trofei", normal), ("Classificata", ranked)):
            if (dataset == "ladder" and label != "Trofei") or (dataset == "ranked" and label != "Classificata"):
                continue
            sections = {k: valid_rows(raw.get(k)) for k in SECTIONS}
            entry["datasets"].append({"label": label, "raw": raw, "sections": sections})
        maps.append(entry)
    # Only request secondary sources for missing Trofei data; absence of Ranked
    # on a live-event map does not imply that the map is in the Ranked pool.
    missing = [entry for entry in maps if dataset != "ranked" and not any(
        entry["datasets"][0]["sections"].values())]
    if secondary and missing:
        with ThreadPoolExecutor(max_workers=3) as pool:
            alternatives = pool.map(secondary, [entry["event"] for entry in missing])
            for entry, alternative in zip(missing, alternatives):
                entry["secondary"] = alternative
    return {"now": now, "events": events, "names": names, "maps": maps, "rotation_missing": False}


def render_report(report, limit=5):
    if report["rotation_missing"]:
        return "Non riesco a verificare gli orari della rotazione attiva. Il catalogo delle mappe non è sufficiente per stabilire quali siano giocabili ora."
    names = report["names"]
    lines = [f"Mappe attive — {report['now'].astimezone(ROME):%d/%m/%Y %H:%M} (Italia)",
             f"Fonte rotazione: Brawl Planet. Mappe verificate: {len(report['events'])}.",
             "Statistiche aggregate della fonte, non solo partite di oggi. Fino a 5 scelte per tabella; per gli individuali con tasso di scelta escludo quelli sotto l'1%. Tutte le righe valide sono nel CSV allegato.",
             "Vittorie, Scelta e Star Player sono metriche distinte. Scelta può superare il 100% nella fonte. Il campione della mappa non è il campione del singolo brawler."]
    for entry in report["maps"]:
        event = entry["event"]
        key = event["event_map_id"]
        raw_mode = next((d["raw"].get("modeFormatted") for d in entry["datasets"] if d["raw"].get("modeFormatted")), MODES.get(event["event_mode"], event["event_mode"]))
        mode = localized(names, "modes", raw_mode)
        map_name = localized(names, "maps", event["event_map"])
        end = event_time(event["end_time"]).astimezone(ROME)
        lines += ["", f"{mode} — {map_name}", f"Evento fino al {end:%d/%m %H:%M} (Italia)"]
        for data in entry["datasets"]:
            lines.append(data["label"] + " — Brawl Planet")
            raw = data["raw"]
            if not any(data["sections"].values()):
                lines.append("Statistiche non disponibili per questa mappa e questo dataset.")
                continue
            if isinstance(raw.get("match_count"), (int, float)):
                lines.append(f"Campione mappa: {raw['match_count']:,} partite".replace(",", "."))
            stamp = raw.get("latest_match_time")
            if isinstance(stamp, (int, float)) and 0 < stamp <= report["now"].timestamp() + 300:
                lines.append(f"Ultima partita nel dataset: {datetime.fromtimestamp(stamp, ROME):%d/%m/%Y %H:%M}")
                if report["now"].timestamp() - stamp > 7 * 86400:
                    lines.append("Dati storici: ultima partita oltre 7 giorni fa. Non descrivono il meta attuale.")
            for section, rows in data["sections"].items():
                if not rows:
                    continue
                lines.append(SECTIONS[section] + ":")
                selected = [row for row in rows if "team" in row or row.get("ur", row.get("use_rate", 1)) >= 1]
                if not selected:
                    lines.append("Nessuna riga supera la soglia di scelta dell'1%; dati nel CSV.")
                lines.extend("• " + row_text(row, names) for row in selected[:limit])
        if entry["secondary"]:
            lines.append(entry["secondary"])
        lines.append("https://www.brawlplanet.com/it/maps/" + key)
    lines += ["", "La rotazione degli eventi e il pool della Classificata sono distinti: gli eventuali dati Classificata qui riportati si riferiscono alle mappe elencate e non certificano il pool Ranked attuale. Le tabelle senza campione individuale non permettono di stabilire l'affidabilità di ogni percentuale."]
    return "\n".join(lines)


def report_csv(report):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["mappa", "modalita", "dataset", "tabella", "brawler_o_squadra", "metrica", "valore", "campione_mappa", "ultima_partita_unix", "fonte"])
    for entry in report["maps"]:
        event = entry["event"]
        for data in entry["datasets"]:
            for section, rows in data["sections"].items():
                for row in rows:
                    identity = row.get("team") or [row.get("brawler", row.get("brawler_name"))]
                    for metric, value in row.items():
                        if metric in ("team", "brawler", "brawler_name"):
                            continue
                        writer.writerow([
                            localized(report["names"], "maps", event["event_map"]),
                            event["event_mode"], data["label"], SECTIONS[section],
                            ", ".join(localized(report["names"], "brawlers", name) for name in identity),
                            metric, value, data["raw"].get("match_count", ""),
                            data["raw"].get("latest_match_time", ""),
                            "https://www.brawlplanet.com/it/maps/" + event["event_map_id"],
                        ])
    return out.getvalue().encode("utf-8-sig")
