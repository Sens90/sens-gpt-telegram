import re
import unicodedata
import requests

BRAWLERS_URL = "https://api.brawlapi.com/v1/brawlers"
ICONS_URL = "https://api.brawlapi.com/v1/icons"
BRAWLZONE_PLAYER_URL = "https://brawlzone.net/player/{tag}"

_brawlers_cache = None
_icons_cache = None

# Alias italiani/varianti usate dalla community. I nomi dei Brawler in gioco
# normalmente coincidono con quelli canonici, ma questo strato rende il comando
# tollerante a forme italianizzate e varianti comuni senza cambiare la reference.
BRAWLER_ALIASES_IT = {
    "bombardino": "Berry",
    "corvo": "Crow",
    "dinamike": "Dynamike",
    "dinamite": "Dynamike",
    "elprimo": "El Primo",
    "franco": "Frank",
    "grommo": "Grom",
    "leone": "Leon",
    "mortisio": "Mortis",
    "signorp": "Mr. P",
    "misterp": "Mr. P",
    "otto bit": "8-Bit",
    "ottobit": "8-Bit",
    "otto-bit": "8-Bit",
    "spina": "Spike",
}


def _norm(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def _catalog(timeout=12):
    global _brawlers_cache, _icons_cache
    if _brawlers_cache is None:
        response = requests.get(BRAWLERS_URL, timeout=timeout)
        response.raise_for_status()
        _brawlers_cache = (response.json() or {}).get("list", [])
    if _icons_cache is None:
        response = requests.get(ICONS_URL, timeout=timeout)
        response.raise_for_status()
        _icons_cache = (response.json() or {}).get("player", {})
    return _brawlers_cache, _icons_cache


def _brawler_by_name(name, brawlers):
    wanted = _norm(name)
    if not wanted:
        return None

    # Prima prova il nome canonico restituito dalla sorgente.
    for item in brawlers:
        if wanted in {_norm(item.get("name")), _norm(item.get("path")), _norm(item.get("hash"))}:
            return item

    # Poi risolve gli alias italiani verso lo stesso Brawler canonico.
    aliases = {_norm(alias): canonical for alias, canonical in BRAWLER_ALIASES_IT.items()}
    canonical = aliases.get(wanted)
    if canonical:
        canonical_norm = _norm(canonical)
        for item in brawlers:
            if canonical_norm == _norm(item.get("name")):
                return item
    return None


def _brawler_by_id(brawler_id, brawlers):
    if brawler_id is None:
        return None
    try:
        target = int(brawler_id)
    except (TypeError, ValueError):
        return None
    for item in brawlers:
        if item.get("id") == target:
            return item
    return None


def _apply_brawler(player, brawler):
    if not brawler:
        return False
    image = brawler.get("imageUrl2") or brawler.get("imageUrl") or brawler.get("imageUrl3")
    if not image:
        return False
    player["profile_brawler"] = brawler.get("name")
    player["profile_brawler_id"] = brawler.get("id")
    player["profile_brawler_image_url"] = image
    return True


def _icon_id_from_brawlzone(tag, timeout=15):
    """Best-effort recovery when the official proxy is unavailable."""
    clean_tag = str(tag or "").upper().replace("#", "").strip()
    if not clean_tag:
        return None
    response = requests.get(
        BRAWLZONE_PLAYER_URL.format(tag=clean_tag),
        headers={"User-Agent": "Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)"},
        timeout=timeout,
    )
    if response.status_code != 200:
        return None
    text = response.text
    patterns = (
        r'"icon"\s*:\s*\{[^{}]{0,250}?"id"\s*:\s*(\d+)',
        r'\\"icon\\"\s*:\s*\{[^{}]{0,250}?\\"id\\"\s*:\s*(\d+)',
        r'"iconId"\s*:\s*(\d+)',
        r'\\"iconId\\"\s*:\s*(\d+)',
        r'profile-icons/(?:regular|borderless)/(\d+)\.(?:png|webp)',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return int(match.group(1))
    return None


def resolve_ai_brawler_reference(player, requested_brawler=None, timeout=15):
    """Attach a real Brawler visual reference to a player dict."""
    try:
        brawlers, icons = _catalog(timeout=timeout)

        if requested_brawler:
            brawler = _brawler_by_name(requested_brawler, brawlers)
            if not brawler:
                return False, f"Brawler '{requested_brawler}' non trovato. Usa il nome italiano o quello ufficiale del Brawler."
            return (_apply_brawler(player, brawler), None)

        icon_id = player.get("icon_id")
        if icon_id is None:
            icon_id = _icon_id_from_brawlzone(player.get("tag"), timeout=timeout)
            if icon_id is not None:
                player["icon_id"] = icon_id

        icon = icons.get(str(icon_id)) if icon_id is not None else None
        if icon:
            player["profile_icon_url"] = icon.get("imageUrl2") or icon.get("imageUrl")
            brawler = _brawler_by_id(icon.get("brawler"), brawlers)
            if brawler and _apply_brawler(player, brawler):
                return True, None
            if player.get("profile_icon_url"):
                return True, None

        existing = player.get("icon_url") or player.get("profile_icon_url")
        if isinstance(existing, str) and existing.startswith(("http://", "https://")):
            return True, None

        return False, "Non riesco a recuperare la foto profilo/Brawler di questo giocatore. Prova con 'profilo ai #TAG con NOME_BRAWLER'."
    except Exception as exc:
        print("AI BRAWLER REFERENCE:", repr(exc), flush=True)
        return False, "Riferimento Brawler temporaneamente non disponibile."
