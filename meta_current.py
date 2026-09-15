"""Deterministic current-meta report built from the BrawlTrack Tier List and map data."""
import re
from urllib.parse import unquote

_BRAWLER_IT = {"Mr P": "Mr. P", "Mister P": "Mr. P"}


def brawler_name_it(name):
    clean = re.sub(r"\s+", " ", str(name or "")).strip()
    return _BRAWLER_IT.get(clean, clean)


def _tier_list(result):
    url = (result.get('url') or '').lower().rstrip('/')
    if not (url.endswith('brawltrack.app/tier-list') or url.endswith('www.brawltrack.app/tier-list')):
        return {}
    text = '\n'.join(str(result.get(k) or '') for k in ('title', 'content', 'raw_content'))
    tiers = {}
    # BrawlTrack can render tier headings as "S Tier", "S-TIER" or "Tier S".
    heading = re.compile(r'(?im)(?:^|\n)\s*(?:#+\s*)?(?:TIER\s+([SABCDF])|([SABCDF])\s*[- ]?TIER)\b')
    matches = list(heading.finditer(text))
    for index, match in enumerate(matches):
        tier = (match.group(1) or match.group(2)).upper()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        names = []
        # Prefer image labels because they identify Brawlers without confusing UI text/classes.
        for name in re.findall(r'Image:\s*([A-Z][A-Z0-9 .\'-]{1,24})(?=\s|$)', block):
            name = brawler_name_it(name.strip().title() if name.isupper() else name.strip())
            if name.casefold() not in {x.casefold() for x in names}:
                names.append(name)
        # Fallback for card rows such as "1 MINA S ...".
        if not names:
            for name in re.findall(r'(?:^|\n)\s*\d+\s+([A-Z][A-Z0-9 .\'-]{1,24})\s+[SABCDF]\b', block, re.I):
                name = brawler_name_it(name.strip().title() if name.isupper() else name.strip())
                if name.casefold() not in {x.casefold() for x in names}:
                    names.append(name)
        if names:
            tiers[tier] = names[:30]
    return tiers


def _map_name(result, text):
    m = re.search(r'brawltrack\.app/pro/maps/([^?#]+)', result.get('url') or '', re.I)
    if m: return unquote(m.group(1)).strip()
    m = re.search(r'(?:^|\n)#\s*([^\n]{2,60})', text)
    return m.group(1).strip() if m else None


def _pro_map(result):
    url = (result.get('url') or '').lower()
    if 'brawltrack.app/pro/maps/' not in url: return None
    text = '\n'.join(str(result.get(k) or '') for k in ('title', 'content', 'raw_content'))
    if not re.search(r'\bPRO\s+ACTIVE\b', text, re.I): return None
    name = _map_name(result, text)
    if not name: return None
    mode_match = re.search(r'Image\s*([A-Za-z ]+?)\s*\n\s*#\s*' + re.escape(name), text, re.I)
    mode = mode_match.group(1).strip() if mode_match else None
    sets_match = re.search(r'SCRIMS\s+(\d+)\s+SETS', text, re.I)
    sets = int(sets_match.group(1)) if sets_match else None
    priority = []
    block = re.search(r'PRIORITY PICKS(.*?)(?:TEAMS ON MAP|COMMON FINAL COMPS|PRO MATCHUP MATRIX)', text, re.I | re.S)
    if block:
        for m in re.finditer(r'\d+\s+([A-Z][A-Z0-9 .\'-]{1,24})\s+[A-S]\s+[A-Z]+\s*[•·]?\s*(\d+(?:\.\d+)?)%\s+USE\s+WIN RATE\s+(\d+(?:\.\d+)?)%', block.group(1), re.I):
            brawler = brawler_name_it(m.group(1).strip().title() if m.group(1).strip().isupper() else m.group(1).strip())
            priority.append((brawler, m.group(2).replace('.', ',') + '%', m.group(3).replace('.', ',') + '%'))
            if len(priority) >= 5: break
    comps = []
    block = re.search(r'COMMON FINAL COMPS(.*?)(?:PRO MATCHUP MATRIX|RECENT MATCH HISTORY)', text, re.I | re.S)
    if block:
        for m in re.finditer(r'(?:Image:\s*)?([A-Z][A-Z0-9 .\'-]+)\s*\+\s*([A-Z][A-Z0-9 .\'-]+)\s*\+\s*([A-Z][A-Z0-9 .\'-]+)\s+(\d+)\s+sets?\s+(\d+(?:\.\d+)?)%\s+WR', block.group(1), re.I):
            team = [brawler_name_it(m.group(i).strip().title() if m.group(i).strip().isupper() else m.group(i).strip()) for i in (1,2,3)]
            if len({x.casefold() for x in team}) != 3: continue
            comps.append((team, int(m.group(4)), m.group(5).replace('.', ',') + '%'))
            if len(comps) >= 3: break
    return {'name':name,'mode':mode,'sets':sets,'priority':priority,'comps':comps}


def render_current_meta(search_data, context='both'):
    tiers = {}
    pro_maps, seen_maps = [], set()
    for result in (search_data or {}).get('results', []):
        url = (result.get('url') or '').lower()
        if '/tier-list' in url:
            parsed = _tier_list(result)
            for tier, names in parsed.items():
                tiers.setdefault(tier, [])
                for name in names:
                    if name.casefold() not in {x.casefold() for x in tiers[tier]}: tiers[tier].append(name)
            continue
        if 'brawltrack.app/pro/maps/' in url:
            item = _pro_map(result)
            if item and item['name'].casefold() not in seen_maps:
                pro_maps.append(item); seen_maps.add(item['name'].casefold())

    # Critical rule: no Tier List = no "meta attuale". Never substitute random brawler pages.
    if not tiers: return None

    lines = ['META ATTUALE — TIER LIST BRAWLTRACK', '', 'Meta generale:']
    for tier in ('S','A','B','C','D','F'):
        names = tiers.get(tier)
        if names: lines.append(f"- Tier {tier}: " + ', '.join(names))

    if pro_maps:
        lines += ['', 'COMPETITIVO / PRO — approfondimento separato:']
        for item in pro_maps[:6]:
            header = item['name']
            if item['mode']: header = item['mode'] + ' — ' + header
            if item['sets'] is not None: header += f" ({item['sets']} set)"
            lines.append('- ' + header)
            if item['priority']:
                lines.append('  Pick prioritari: ' + '; '.join(f'{n} — Utilizzo {u}, Vittorie {w}' for n,u,w in item['priority']))
            if item['comps']:
                lines.append('  Composizioni finali: ' + '; '.join(f"{' + '.join(team)} — {sets} set, Vittorie {wr}" for team,sets,wr in item['comps']))

    lines += ['', 'Fonte meta generale: Tier List BrawlTrack. Le statistiche dei singoli Brawler non vengono più usate per decidere chi è nel meta.']
    return '\n'.join(lines)
