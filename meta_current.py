"""Deterministic current-meta report built only from BrawlTrack search results."""
import re
from urllib.parse import unquote

_BRAWLER_IT = {"Mr P": "Mr. P", "Mister P": "Mr. P"}


def brawler_name_it(name):
    clean = re.sub(r"\s+", " ", str(name or "")).strip()
    return _BRAWLER_IT.get(clean, clean)


def _pct(text, label):
    m = re.search(rf"\b{label}\b\s*[:\-]?\s*(\d{{1,3}}(?:[.,]\d+)?)\s*%", text, re.I)
    if not m:
        return None
    value = float(m.group(1).replace(',', '.'))
    if not 0 <= value <= 100:
        return None
    return f"{value:.2f}".rstrip('0').rstrip('.').replace('.', ',') + '%'


def _matches(text):
    m = re.search(r"Total\s+Matches\s+Tracked\s*([\d.,]+)\s*Battles", text, re.I)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else None


def _page_name(text):
    patterns = [
        r"Back\s+to\s+Roster\s+(?:Image:\s*)?([^\n]+?)\s+#\s*([A-Z0-9 .'-]+)",
        r"(?:^|\n)#\s*([A-Z][A-Z0-9 .'-]{1,30})(?:\n|\r)",
        r"(?:Image:\s*)([A-Z][A-Za-z0-9 .'-]{1,30})(?:\n|\r)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.M)
        if m:
            candidate = m.group(m.lastindex or 1).strip()
            if candidate and not candidate.isdigit():
                return brawler_name_it(candidate.title() if candidate.isupper() else candidate)
    return None


def _name(result, text):
    visible = _page_name(text)
    if visible:
        return visible
    url = result.get('url') or ''
    m = re.search(r'brawltrack\.app/brawlers/([^/?#]+)', url, re.I)
    if m and not m.group(1).isdigit():
        return brawler_name_it(m.group(1).replace('-', ' ').title())
    title = re.sub(r'\s*[-|·].*$', '', result.get('title') or '').strip()
    title = re.sub(r'\s+Stats(?:,.*)?$', '', title, flags=re.I).strip()
    return brawler_name_it(title) if title and not title.isdigit() else None


def _map_name(result, text):
    m = re.search(r'brawltrack\.app/pro/maps/([^?#]+)', result.get('url') or '', re.I)
    if m:
        return unquote(m.group(1)).strip()
    m = re.search(r'(?:^|\n)#\s*([^\n]{2,60})', text)
    return m.group(1).strip() if m else None


def _pro_map(result):
    url = (result.get('url') or '').lower()
    if 'brawltrack.app/pro/maps/' not in url:
        return None
    text = '\n'.join(str(result.get(k) or '') for k in ('title', 'content', 'raw_content'))
    if not re.search(r'\bPRO\s+ACTIVE\b', text, re.I):
        return None
    name = _map_name(result, text)
    if not name:
        return None
    mode_match = re.search(r'Image\s*([A-Za-z ]+?)\s*\n\s*#\s*' + re.escape(name), text, re.I)
    mode = mode_match.group(1).strip() if mode_match else None
    sets_match = re.search(r'SCRIMS\s+(\d+)\s+SETS', text, re.I)
    sets = int(sets_match.group(1)) if sets_match else None

    priority = []
    block = re.search(r'PRIORITY PICKS(.*?)(?:TEAMS ON MAP|COMMON FINAL COMPS|PRO MATCHUP MATRIX)', text, re.I | re.S)
    if block:
        for m in re.finditer(r'\d+\s+([A-Z][A-Z0-9 .\'-]{1,24})\s+[A-S]\s+[A-Z]+\s*[•·]?\s*(\d+(?:\.\d+)?)%\s+USE\s+WIN RATE\s+(\d+(?:\.\d+)?)%', block.group(1), re.I):
            brawler = brawler_name_it(m.group(1).strip().title())
            priority.append((brawler, m.group(2).replace('.', ',') + '%', m.group(3).replace('.', ',') + '%'))
            if len(priority) >= 5:
                break

    comps = []
    block = re.search(r'COMMON FINAL COMPS(.*?)(?:PRO MATCHUP MATRIX|RECENT MATCH HISTORY)', text, re.I | re.S)
    if block:
        for m in re.finditer(r'(?:Image:\s*)?([A-Z][A-Z0-9 .\'-]+)\s*\+\s*([A-Z][A-Z0-9 .\'-]+)\s*\+\s*([A-Z][A-Z0-9 .\'-]+)\s+(\d+)\s+sets?\s+(\d+(?:\.\d+)?)%\s+WR', block.group(1), re.I):
            team = [brawler_name_it(m.group(i).strip().title()) for i in (1, 2, 3)]
            if len({x.casefold() for x in team}) != 3:
                continue
            comps.append((team, int(m.group(4)), m.group(5).replace('.', ',') + '%'))
            if len(comps) >= 3:
                break
    return {'name': name, 'mode': mode, 'sets': sets, 'priority': priority, 'comps': comps}


def render_current_meta(search_data, context='both'):
    rows, seen = [], set()
    pro_maps, seen_maps = [], set()
    for result in (search_data or {}).get('results', []):
        url = (result.get('url') or '').lower()
        if 'brawltrack.app/pro/maps/' in url:
            item = _pro_map(result)
            if item and item['name'].casefold() not in seen_maps:
                pro_maps.append(item)
                seen_maps.add(item['name'].casefold())
            continue
        if 'brawltrack.app/brawlers/' not in url:
            continue
        text = '\n'.join(str(result.get(k) or '') for k in ('title', 'content', 'raw_content'))
        name = _name(result, text)
        if not name or name.casefold() in seen:
            continue
        win = _pct(text, r'Win\s+Rate')
        use = _pct(text, r'Meta\s+Usage')
        star = _pct(text, r'Star\s+Rate')
        matches = _matches(text)
        if not any((win, use, star)):
            continue
        rows.append((name, win, use, star, matches))
        seen.add(name.casefold())
        if len(rows) >= 10:
            continue

    if not rows and not pro_maps:
        return None

    lines = ['META ATTUALE - DATI BRAWLTRACK']
    if rows:
        lines += ['', 'Statistiche globali Brawler:']
        for name, win, use, star, matches in rows[:10]:
            metrics = []
            if win: metrics.append('Vittorie ' + win)
            if use: metrics.append('Utilizzo ' + use)
            if star: metrics.append('Miglior Star Player ' + star)
            if matches is not None: metrics.append('Partite ' + f'{matches:,}'.replace(',', '.'))
            lines.append('- ' + brawler_name_it(name) + ': ' + ' | '.join(metrics))

    if pro_maps:
        lines += ['', 'COMPETITIVO / PRO — separato dalla Classificata:']
        for item in pro_maps[:6]:
            header = item['name']
            if item['mode']:
                header = item['mode'] + ' — ' + header
            if item['sets'] is not None:
                header += f" ({item['sets']} set)"
            lines.append('- ' + header)
            if item['priority']:
                lines.append('  Pick prioritari: ' + '; '.join(f'{n} — Utilizzo {u}, Vittorie {w}' for n, u, w in item['priority']))
            if item['comps']:
                lines.append('  Composizioni finali: ' + '; '.join(f"{' + '.join(team)} — {sets} set, Vittorie {wr}" for team, sets, wr in item['comps']))

    lines += ['', 'Ladder / Classificata:']
    if context == 'ladder':
        lines.append('- Ladder: vengono mostrati come Ladder solo dati esplicitamente identificati dalla fonte come Ladder.')
    elif context == 'ranked':
        lines.append('- Classificata: vengono mostrati come Classificata solo dati esplicitamente identificati dalla fonte come Classificata.')
    else:
        lines.append('- Le statistiche globali non vengono attribuite a Ladder o Classificata senza un dataset esplicito. Il competitivo/pro resta una sezione separata.')
    lines += ['', 'Bilanciamenti e Buffie restano separati e vengono citati solo quando verificati da fonti ufficiali.']
    return '\n'.join(lines)
