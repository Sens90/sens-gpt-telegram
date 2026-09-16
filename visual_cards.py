import io
from functools import lru_cache

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

CLUB_OVERRIDES = {"2VQYLG0RU8": "TALENTI ABUSIVI"}

STAT_LABELS = {
    "trofei": "TROFEI", "brawler": "BRAWLER", "livello": "LIVELLO ACCOUNT",
    "prestigio": "PRESTIGIO", "3v3": "VITTORIE 3V3", "solo": "VITTORIE SOLO",
    "duo": "VITTORIE DUO", "classificata": "CLASSIFICATA ATTUALE",
    "classificata stagione": "RECORD CLASSIFICATA STAGIONE",
    "classificata carriera": "RECORD CLASSIFICATA CARRIERA",
}

STAT_ICONS = {
    "trofei": "https://cdn.brawlify.com/icon/trophy.png",
    "brawler": "https://cdn.brawlify.com/icon/brawler.png",
    "livello": "https://cdn.brawlify.com/icon/level.png",
    "prestigio": "https://cdn.brawlify.com/icon/prestige.png",
    "3v3": "https://cdn.brawlify.com/icon/3v3.png",
    "solo": "https://cdn.brawlify.com/icon/solo-showdown.png",
    "duo": "https://cdn.brawlify.com/icon/duo-showdown.png",
}

# Current official major-division score bands (Supercell Support, Sep 2026).
# Badge artwork is drawn locally so a remote CDN failure can never remove the division logo.
RANK_BANDS = [
    (11250, "PRO", (246, 75, 105, 255)),
    (8250, "CAMPIONI", (241, 196, 65, 255)),
    (6000, "LEGGENDE", (225, 65, 93, 255)),
    (4500, "MITI", (181, 84, 226, 255)),
    (3000, "DIAMANTE", (69, 190, 235, 255)),
    (1500, "ORO", (244, 184, 45, 255)),
    (750, "ARGENTO", (185, 198, 215, 255)),
    (0, "BRONZO", (183, 119, 73, 255)),
]


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


@lru_cache(maxsize=256)
def _remote_bytes(url):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=6, headers={"User-Agent": "SensGPT/1.0"})
        r.raise_for_status()
        return r.content
    except Exception as exc:
        print("VISUAL ASSET ERROR:", url, repr(exc), flush=True)
        return None


def _remote_image(url, size):
    data = _remote_bytes(url)
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("RGBA")
        return ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
    except Exception as exc:
        print("VISUAL IMAGE ERROR:", repr(exc), flush=True)
        return None


def _fmt(value):
    if value is None:
        return "Non disponibile"
    try:
        if isinstance(value, int) or str(value).isdigit():
            return f"{int(value):,}".replace(",", ".")
    except Exception:
        pass
    return str(value)


def _change(value):
    if value is None:
        return "Storico non disponibile"
    try:
        value = int(value)
        return f"+{value}" if value > 0 else str(value)
    except Exception:
        return str(value)


def _resolved_club(player):
    tag = str(player.get("tag") or "").upper().replace("#", "")
    if tag in CLUB_OVERRIDES:
        return CLUB_OVERRIDES[tag]
    club = player.get("club_name") or player.get("club")
    if isinstance(club, dict):
        club = club.get("name")
    return club or "Senza club / non disponibile"


def _paste_icon(canvas, url, xy, size):
    icon = _remote_image(url, size)
    if icon:
        canvas.alpha_composite(icon, xy)
        return True
    return False


def _rank_info(score):
    try:
        score = int(score)
    except (TypeError, ValueError):
        return None, None
    for floor, name, color in RANK_BANDS:
        if score >= floor:
            return name, color
    return "BRONZO", RANK_BANDS[-1][2]


def _draw_rank_badge(canvas, xy, score, size=50):
    name, color = _rank_info(score)
    if not name:
        return False
    x, y = xy
    d = ImageDraw.Draw(canvas)
    # Compact shield-style division emblem, always available locally.
    points = [(x+size//2, y), (x+size, y+size//5), (x+size*4//5, y+size*4//5),
              (x+size//2, y+size), (x+size//5, y+size*4//5), (x, y+size//5)]
    d.polygon(points, fill=color, outline=(255,255,255,230))
    letter = "P" if name == "PRO" else name[0]
    f = _font(max(15, size//2), True)
    box = d.textbbox((0,0), letter, font=f)
    d.text((x+(size-(box[2]-box[0]))/2, y+(size-(box[3]-box[1]))/2-2), letter, font=f, fill=(20,24,36,255))
    return True


def _rank_score(player, key):
    mapping = {
        "classificata": "ranked_current_elo",
        "classificata stagione": "ranked_season_peak_elo",
        "classificata carriera": "ranked_career_peak_elo",
    }
    return player.get(mapping[key])


def _rank_text(player, key, fallback):
    score = _rank_score(player, key)
    name, _ = _rank_info(score)
    if score is not None:
        return f"{_fmt(score)} ELO" + (f" · {name.title()}" if name else "")
    return _fmt(fallback)


def build_player_stats_card(player, changes=None, title="STATISTICHE GIOCATORE"):
    changes = changes or {}
    width, height = 1080, 1180
    im = Image.new("RGBA", (width, height), (22, 27, 42, 255))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((28, 28, width-28, height-28), radius=36, fill=(34, 43, 67, 255), outline=(238, 184, 45, 255), width=4)
    d.text((54, 52), title, font=_font(42, True), fill="white")

    y = 125
    if _paste_icon(im, player.get("icon_url"), (55, y), 118): x_name = 195
    else: x_name = 55
    d.text((x_name, y+5), str(player.get("name") or "Giocatore"), font=_font(38, True), fill="white")
    d.text((x_name, y+55), f"Tag: {player.get('tag') or '-'}", font=_font(25), fill=(215, 220, 235, 255))
    d.text((x_name, y+90), f"Club: {_resolved_club(player)}", font=_font(25, True), fill=(238, 184, 45, 255))

    rows = [
        ("Trofei", player.get("trophies"), "trofei"), ("Brawler", player.get("brawlers"), "brawler"),
        ("Livello", player.get("level"), "livello"), ("Prestigio", player.get("prestige"), "prestigio"),
        ("Ranked attuale", player.get("ranked_current"), "classificata"),
        ("Record stagione", player.get("ranked_season_peak"), "classificata stagione"),
        ("Record massimo", player.get("ranked_career_peak") or player.get("ranked_peak"), "classificata carriera"),
        ("Vittorie 3v3", player.get("wins_3v3"), "3v3"), ("Vittorie Solo", player.get("wins_solo"), "solo"),
        ("Vittorie Duo", player.get("wins_duo"), "duo"),
    ]
    y = 285
    for label, value, key in rows:
        if key.startswith("classificata"):
            score = _rank_score(player, key)
            _draw_rank_badge(im, (62, y-5), score, 44)
            display = _rank_text(player, key, value)
        else:
            _paste_icon(im, STAT_ICONS.get(key), (62, y-3), 42)
            display = _fmt(value)
        d.text((118, y), label, font=_font(25, True), fill=(205, 213, 235, 255))
        d.text((590, y), display, font=_font(25, True), fill="white")
        y += 58

    d.line((55, y+4, width-55, y+4), fill=(92, 105, 140, 255), width=2)
    y += 28
    d.text((55, y), "ANDAMENTO TROFEI", font=_font(28, True), fill=(238, 184, 45, 255))
    y += 50
    for label, key in [("Oggi", "today"), ("7 giorni", "7d"), ("15 giorni", "15d"), ("30 giorni", "30d"), ("90 giorni", "90d")]:
        d.text((70, y), label, font=_font(23, True), fill=(205, 213, 235, 255))
        d.text((350, y), _change(changes.get(key)), font=_font(23), fill="white")
        y += 43

    out = io.BytesIO(); im.convert("RGB").save(out, format="JPEG", quality=91, optimize=True); out.seek(0); out.name="statistiche.jpg"; return out


def build_ranking_card(title, rows, stat_key, start_position=1):
    row_h=102; width=1080; height=145+row_h*len(rows)+40
    im=Image.new("RGBA",(width,height),(22,27,42,255)); d=ImageDraw.Draw(im)
    d.rounded_rectangle((24,24,width-24,height-24),radius=32,fill=(34,43,67,255),outline=(238,184,45,255),width=4)
    if stat_key.startswith("classificata"):
        d.text((55,53), title, font=_font(31,True), fill="white")
    else:
        _paste_icon(im,STAT_ICONS.get(stat_key),(55,48),58); d.text((130,53),title,font=_font(31,True),fill="white")
    y=132
    for offset,row in enumerate(rows):
        pos=start_position+offset
        d.text((55,y+25),f"{pos}.",font=_font(29,True),fill=(238,184,45,255))
        _paste_icon(im,row.get("icon_url"),(120,y+10),72)
        name=str(row.get("name") or row.get("tag") or "-")
        if len(name)>27: name=name[:26]+"…"
        d.text((210,y+12),name,font=_font(27,True),fill="white")
        d.text((210,y+50),str(row.get("tag") or ""),font=_font(19),fill=(175,185,210,255))
        if stat_key.startswith("classificata"):
            score=row.get("value")
            _draw_rank_badge(im,(760,y+20),score,58)
            value=f"{_fmt(score)} ELO"
            box=d.textbbox((0,0),value,font=_font(24,True))
            d.text((1005-(box[2]-box[0]),y+29),value,font=_font(24,True),fill=(238,184,45,255))
        else:
            value=_fmt(row.get("value")); box=d.textbbox((0,0),value,font=_font(26,True)); d.text((1005-(box[2]-box[0]),y+29),value,font=_font(26,True),fill=(238,184,45,255))
        y+=row_h
    out=io.BytesIO(); im.convert("RGB").save(out,format="JPEG",quality=90,optimize=True); out.seek(0); out.name="classifica.jpg"; return out


async def send_stat_ranking_cards(message, context, community, stat_key, club_name=None):
    definition=community.STAT_DEFS.get(stat_key)
    if not definition:
        await message.reply_text("Statistica non riconosciuta."); return
    rows=community._stat_rows(message.chat_id,stat_key,club_name)
    label=definition[1]; scope=club_name or "COMMUNITY"
    if not rows:
        await message.reply_text(f"Nessun dato disponibile per {label} in {scope}."); return
    title=f"{scope} - {label.upper()}"
    for start in range(0,min(len(rows),60),10):
        chunk=rows[start:start+10]; card=build_ranking_card(title,chunk,stat_key,start_position=start+1)
        await context.bot.send_photo(chat_id=message.chat_id,photo=card)
