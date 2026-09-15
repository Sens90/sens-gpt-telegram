"""Deterministic current-meta report built only from BrawlTrack search results."""
import re

# Nomi mostrati come nel client italiano quando differiscono dalla fonte/slug.
# I nomi propri che non cambiano restano invariati.
_BRAWLER_IT = {
    "Mr P": "Mr. P",
    "Mister P": "Mr. P",
}


def brawler_name_it(name):
    clean = re.sub(r"\s+", " ", str(name or "")).strip()
    return _BRAWLER_IT.get(clean, clean)


def _pct(text, labels):
    for label in labels:
        m = re.search(rf"{label}\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*%", text, re.I)
        if m:
            return m.group(1).replace('.', ',') + '%'
    return None


def _name(result):
    url = result.get('url') or ''
    m = re.search(r'brawltrack\.app/brawlers/([^/?#]+)', url, re.I)
    if m:
        return brawler_name_it(m.group(1).replace('-', ' ').title())
    title = re.sub(r'\s*[-|].*$', '', result.get('title') or '').strip()
    return brawler_name_it(title) if title else None


def render_current_meta(search_data, context='both'):
    """Return a compact verified report, or None if BrawlTrack data is insufficient."""
    rows = []
    seen = set()
    for result in (search_data or {}).get('results', []):
        url = (result.get('url') or '').lower()
        if 'brawltrack.app/brawlers/' not in url:
            continue
        text = '\n'.join(str(result.get(k) or '') for k in ('title','content','raw_content'))
        if re.search(r'\bTBD\b', text, re.I):
            continue
        name = _name(result)
        if not name or name.casefold() in seen:
            continue
        win = _pct(text, [r'win\s*rate', r'tasso\s+di\s+vittoria', r'vittorie'])
        use = _pct(text, [r'use\s*rate', r'usage', r'meta\s+usage', r'utilizzo', r'scelta'])
        star = _pct(text, [r'star\s*rate', r'star\s*player', r'miglior\s+star\s+player', r'stella'])
        if not any((win, use, star)):
            continue
        rows.append((name, win, use, star))
        seen.add(name.casefold())
        if len(rows) >= 8:
            break
    if not rows:
        return None
    lines = ['META ATTUALE - DATI BRAWLTRACK', '', 'Brawler con statistiche verificabili:']
    for name, win, use, star in rows:
        metrics = []
        if win: metrics.append('Vittorie ' + win)
        if use: metrics.append('Utilizzo ' + use)
        if star: metrics.append('Miglior Star Player ' + star)
        lines.append('- ' + brawler_name_it(name) + ': ' + ' | '.join(metrics))
    lines += ['', 'Contesti:']
    if context == 'ladder':
        lines.append('- Ladder: vengono mostrati solo dati identificati come Ladder.')
    elif context == 'ranked':
        lines.append('- Classificata: vengono mostrati solo dati identificati come Classificata.')
    else:
        lines.append('- Ladder e Classificata non vengono fusi. I dati senza contesto verificato non vengono attribuiti a uno dei due.')
    lines += ['', 'Bilanciamenti e Buffie sono categorie separate e vengono mostrati solo se verificati da fonti ufficiali.']
    return '\n'.join(lines)
