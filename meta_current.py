"""Deterministic current-meta report built only from BrawlTrack search results."""
import re

_BRAWLER_IT = {"Mr P": "Mr. P", "Mister P": "Mr. P"}


def brawler_name_it(name):
    clean = re.sub(r"\s+", " ", str(name or "")).strip()
    return _BRAWLER_IT.get(clean, clean)


def _pct(text, label):
    # BrawlTrack profile pages expose the global block as:
    # Win Rate 52.01% / Meta Usage 0.37% / Star Rate 7.13%.
    # Match the exact label so build Use Rate or mode percentages cannot leak in.
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
    # Prefer the visible BrawlTrack H1/roster name. Numeric URLs are internal IDs.
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
    if m:
        slug = m.group(1)
        # Never expose BrawlTrack's numeric internal ID as a Brawler name.
        if not slug.isdigit():
            return brawler_name_it(slug.replace('-', ' ').title())
    title = re.sub(r'\s*[-|·].*$', '', result.get('title') or '').strip()
    title = re.sub(r'\s+Stats(?:,.*)?$', '', title, flags=re.I).strip()
    if title and not title.isdigit():
        return brawler_name_it(title)
    return None


def render_current_meta(search_data, context='both'):
    """Return a compact verified report, or None if BrawlTrack data is insufficient."""
    rows, seen = [], set()
    for result in (search_data or {}).get('results', []):
        url = (result.get('url') or '').lower()
        if 'brawltrack.app/brawlers/' not in url:
            continue
        text = '\n'.join(str(result.get(k) or '') for k in ('title', 'content', 'raw_content'))
        name = _name(result, text)
        if not name or name.casefold() in seen:
            continue

        # Parse each metric independently. A TBD elsewhere on the page must not
        # discard valid metrics, but TBD itself is never converted into a value.
        win = _pct(text, r'Win\s+Rate')
        use = _pct(text, r'Meta\s+Usage')
        star = _pct(text, r'Star\s+Rate')
        matches = _matches(text)
        if not any((win, use, star)):
            continue
        rows.append((name, win, use, star, matches))
        seen.add(name.casefold())
        if len(rows) >= 8:
            break

    if not rows:
        return None

    lines = ['META ATTUALE - DATI BRAWLTRACK', '', 'Brawler con statistiche globali verificabili:']
    for name, win, use, star, matches in rows:
        metrics = []
        if win: metrics.append('Vittorie ' + win)
        if use: metrics.append('Utilizzo ' + use)
        if star: metrics.append('Miglior Star Player ' + star)
        if matches is not None: metrics.append('Partite ' + f'{matches:,}'.replace(',', '.'))
        lines.append('- ' + brawler_name_it(name) + ': ' + ' | '.join(metrics))

    lines += ['', 'Contesti:']
    if context == 'ladder':
        lines.append('- Ladder: mostro soltanto statistiche esplicitamente identificate come Ladder.')
    elif context == 'ranked':
        lines.append('- Classificata: mostro soltanto statistiche esplicitamente identificate come Classificata.')
    else:
        lines.append('- Queste sono statistiche globali BrawlTrack: non vengono spacciate per Ladder o Classificata se la fonte non identifica il dataset.')
    lines += ['', 'Bilanciamenti e Buffie restano separati e vengono citati solo quando verificati da fonti ufficiali.']
    return '\n'.join(lines)
