"""Deterministic current-meta report from verified BrawlTrack data only."""
import re
from urllib.parse import unquote

_BRAWLER_IT = {"Mr P": "Mr. P", "Mister P": "Mr. P"}


def brawler_name_it(name):
    clean = re.sub(r"\s+", " ", str(name or "")).strip()
    return _BRAWLER_IT.get(clean, clean)


def _text(result):
    return "\n".join(str(result.get(k) or "") for k in ("title", "content", "raw_content"))


def _tier_list(result):
    url = (result.get("url") or "").lower().rstrip("/")
    if not url.endswith("brawltrack.app/tier-list"):
        return {}
    text = _text(result)
    tiers = {}
    heading = re.compile(r"(?im)(?:^|\n)\s*(?:#+\s*)?(?:TIER\s+([SABCDF])|([SABCDF])\s*[- ]?TIER)\b")
    matches = list(heading.finditer(text))
    for index, match in enumerate(matches):
        tier = (match.group(1) or match.group(2)).upper()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        names = []
        for name in re.findall(r"Image:\s*([A-Z][A-Z0-9 .'-]{1,24})(?=\s|$)", block):
            name = brawler_name_it(name.strip().title() if name.isupper() else name.strip())
            if name.casefold() not in {x.casefold() for x in names}: names.append(name)
        if not names:
            for name in re.findall(r"(?:^|\n)\s*\d+\s+([A-Z][A-Z0-9 .'-]{1,24})\s+[SABCDF]\b", block, re.I):
                name = brawler_name_it(name.strip().title() if name.isupper() else name.strip())
                if name.casefold() not in {x.casefold() for x in names}: names.append(name)
        if names: tiers[tier] = names[:30]
    return tiers


def _brawler_card(result):
    url = (result.get("url") or "").lower()
    if "brawltrack.app/brawlers/" not in url: return None
    text = _text(result)
    name_m = re.search(r"(?m)^#\s+([A-Z0-9 .'-]{2,30})\s*$", text)
    rank_m = re.search(r"\bRank\s+([SABCDF]|TBD)\b", text, re.I)
    if not name_m or not rank_m: return None
    def metric(label):
        m = re.search(re.escape(label) + r"\s*(?:\n|\s)+(?:(TBD)|(\d+(?:\.\d+)?)%)", text, re.I)
        if not m or m.group(1): return None
        return m.group(2).replace(".", ",") + "%"
    name = brawler_name_it(name_m.group(1).strip().title() if name_m.group(1).strip().isupper() else name_m.group(1).strip())
    return {"name": name, "rank": rank_m.group(1).upper(), "win": metric("Win Rate"), "usage": metric("Meta Usage"), "star": metric("Star Rate")}


def _map_name(result, text):
    m = re.search(r"brawltrack\.app/pro/maps/([^?#]+)", result.get("url") or "", re.I)
    if m: return unquote(m.group(1)).strip()
    m = re.search(r"(?:^|\n)#\s*([^\n]{2,60})", text)
    return m.group(1).strip() if m else None


def _pro_map(result):
    url = (result.get("url") or "").lower()
    if "brawltrack.app/pro/maps/" not in url: return None
    text = _text(result)
    if not re.search(r"\bPRO\s+ACTIVE\b", text, re.I): return None
    name = _map_name(result, text)
    if not name: return None
    mode_match = re.search(r"Image\s*([A-Za-z ]+?)\s*\n\s*#\s*" + re.escape(name), text, re.I)
    mode = mode_match.group(1).strip() if mode_match else None
    sets_match = re.search(r"SCRIMS\s+(\d+)\s+SETS", text, re.I)
    priority = []
    block = re.search(r"PRIORITY PICKS(.*?)(?:TEAMS ON MAP|COMMON FINAL COMPS|PRO MATCHUP MATRIX)", text, re.I | re.S)
    if block:
        for m in re.finditer(r"\d+\s+([A-Z][A-Z0-9 .'-]{1,24})\s+([SABCDF])\s+[A-Z]+\s*[•·]?\s*(\d+(?:\.\d+)?)%\s+USE\s+WIN RATE\s+(\d+(?:\.\d+)?)%", block.group(1), re.I):
            b = brawler_name_it(m.group(1).strip().title() if m.group(1).strip().isupper() else m.group(1).strip())
            priority.append((b, m.group(2).upper(), m.group(3).replace(".", ",")+"%", m.group(4).replace(".", ",")+"%"))
            if len(priority) >= 5: break
    comps = []
    block = re.search(r"COMMON FINAL COMPS(.*?)(?:PRO MATCHUP MATRIX|RECENT MATCH HISTORY)", text, re.I | re.S)
    if block:
        for m in re.finditer(r"(?:Image:\s*)?([A-Z][A-Z0-9 .'-]+)\s*\+\s*([A-Z][A-Z0-9 .'-]+)\s*\+\s*([A-Z][A-Z0-9 .'-]+)\s+(\d+)\s+sets?\s+(\d+(?:\.\d+)?)%\s+WR", block.group(1), re.I):
            team=[brawler_name_it(m.group(i).strip().title() if m.group(i).strip().isupper() else m.group(i).strip()) for i in (1,2,3)]
            if len({x.casefold() for x in team}) == 3: comps.append((team,int(m.group(4)),m.group(5).replace(".",",")+"%"))
            if len(comps)>=3: break
    return {"name":name,"mode":mode,"sets":int(sets_match.group(1)) if sets_match else None,"priority":priority,"comps":comps}


def render_current_meta(search_data, context="both"):
    tiers, cards, pro_maps, seen_maps = {}, {}, [], set()
    for result in (search_data or {}).get("results", []):
        url=(result.get("url") or "").lower()
        if "/tier-list" in url:
            for tier,names in _tier_list(result).items():
                tiers.setdefault(tier,[])
                for name in names:
                    if name.casefold() not in {x.casefold() for x in tiers[tier]}: tiers[tier].append(name)
        elif "brawltrack.app/brawlers/" in url:
            card=_brawler_card(result)
            if card: cards[card["name"].casefold()]=card
        elif "brawltrack.app/pro/maps/" in url:
            item=_pro_map(result)
            if item and item["name"].casefold() not in seen_maps: pro_maps.append(item);seen_maps.add(item["name"].casefold())
    if not tiers: return None

    lines=["META ATTUALE — BRAWLTRACK","","Tier List generale:"]
    for tier in ("S","A","B","C","D","F"):
        names=tiers.get(tier)
        if not names: continue
        lines.append(f"- Tier {tier}: "+", ".join(names))
        for name in names[:8]:
            card=cards.get(name.casefold())
            # Never let a card override the Tier List. If ranks disagree, show neither claim as a synthesized truth.
            if card and card["rank"] == tier:
                stats=[]
                if card["win"]: stats.append("Vittorie "+card["win"])
                if card["usage"]: stats.append("Utilizzo "+card["usage"])
                if card["star"]: stats.append("Miglior Star Player "+card["star"])
                if stats: lines.append("  • "+name+": " + " · ".join(stats))

    if pro_maps:
        lines += ["","COMPETITIVO / PRO — separato dal meta generale:"]
        for item in pro_maps[:6]:
            header=(item["mode"]+" — " if item["mode"] else "")+item["name"]
            if item["sets"] is not None: header += f" ({item['sets']} set)"
            lines.append("- "+header)
            if item["priority"]: lines.append("  Pick prioritari: "+"; ".join(f"{n} (Tier {t}) — Utilizzo {u}, Vittorie {w}" for n,t,u,w in item["priority"]))
            if item["comps"]: lines.append("  Composizioni finali: "+"; ".join(f"{' + '.join(team)} — {sets} set, Vittorie {wr}" for team,sets,wr in item["comps"]))

    lines += ["","Regole dati: il Tier viene esclusivamente dalla Tier List BrawlTrack. Win rate, utilizzo e Miglior Star Player possono solo arricchire un Brawler già presente nel Tier corretto. Nessun buff, nerf, rework o giudizio per ruolo viene dedotto dalle statistiche."]
    return "\n".join(lines)
