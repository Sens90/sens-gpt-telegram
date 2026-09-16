import base64
import io
import os
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageFont

from ai_profile_experience import build_visual_prompt, profile_brawler_reference

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
AI_PROFILE_MODEL = os.environ.get("AI_PROFILE_MODEL", "google/gemini-3.1-flash-lite-image")
MONTHLY_LIMIT = int(os.environ.get("AI_PROFILE_MONTHLY_LIMIT", "3"))


def _headers():
    key = os.environ.get("AI_GATEWAY_API_KEY")
    if not key:
        raise RuntimeError("AI_GATEWAY_API_KEY non configurata")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _supabase_headers():
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY non configurata")
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _month_key():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def quota_status(telegram_user_id):
    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not base:
        return {"unlimited": False, "used": 0, "limit": MONTHLY_LIMIT}
    headers = _supabase_headers()
    privileged = requests.get(
        f"{base}/rest/v1/ai_profile_privileged_users",
        params={"telegram_user_id": f"eq.{int(telegram_user_id)}", "select": "telegram_user_id", "limit": "1"},
        headers=headers, timeout=10,
    )
    privileged.raise_for_status()
    if privileged.json():
        return {"unlimited": True, "used": 0, "limit": None}
    usage = requests.get(
        f"{base}/rest/v1/ai_profile_usage",
        params={"telegram_user_id": f"eq.{int(telegram_user_id)}", "month_key": f"eq.{_month_key()}", "select": "successful_generations", "limit": "1"},
        headers=headers, timeout=10,
    )
    usage.raise_for_status()
    rows = usage.json()
    used = int(rows[0]["successful_generations"]) if rows else 0
    return {"unlimited": False, "used": used, "limit": MONTHLY_LIMIT}


def consume_quota(telegram_user_id):
    status = quota_status(telegram_user_id)
    if status["unlimited"]:
        return status
    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    headers = _supabase_headers()
    new_value = status["used"] + 1
    response = requests.post(
        f"{base}/rest/v1/ai_profile_usage",
        params={"on_conflict": "telegram_user_id,month_key"},
        headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={"telegram_user_id": int(telegram_user_id), "month_key": _month_key(), "successful_generations": new_value, "updated_at": datetime.now(timezone.utc).isoformat()},
        timeout=10,
    )
    response.raise_for_status()
    return {"unlimited": False, "used": new_value, "limit": MONTHLY_LIMIT}


def _reference_data_url(url):
    if not url:
        return None
    response = requests.get(url, headers={"User-Agent": "SensGPT-TitaniAbusivi/1.0"}, timeout=15)
    response.raise_for_status()
    ctype = response.headers.get("Content-Type", "image/png").split(";")[0]
    return f"data:{ctype};base64,{base64.b64encode(response.content).decode()}"


def generate_scene(player, category="random"):
    prompt, scene = build_visual_prompt(player, category)
    reference = profile_brawler_reference(player)
    if not reference:
        raise RuntimeError("Foto profilo Brawler non disponibile: generazione annullata per non inventare il personaggio.")
    data_url = _reference_data_url(reference)
    content = [
        {"type": "text", "text": prompt + " Usa l'immagine allegata come reference VISIVA OBBLIGATORIA del personaggio. Mantieni il Brawler il piu fedele possibile all'originale. Prevedi nella composizione uno spazio evidente per il branding TITANI ABUSIVI; il logo ufficiale verra applicato dal bot dopo la generazione. Non copiare testo o numeri presenti nella reference."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    payload = {"model": AI_PROFILE_MODEL, "messages": [{"role": "user", "content": content}], "modalities": ["text", "image"], "stream": False}
    response = requests.post(GATEWAY_URL, headers=_headers(), json=payload, timeout=120)
    if response.status_code >= 400:
        raise RuntimeError(f"AI Gateway HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    message = ((data.get("choices") or [{}])[0].get("message") or {})
    for item in message.get("images") or []:
        url = ((item.get("image_url") or {}).get("url") if isinstance(item, dict) else None)
        if not url:
            continue
        if url.startswith("data:image/") and "," in url:
            return base64.b64decode(url.split(",", 1)[1]), scene
        if url.startswith("http"):
            image = requests.get(url, timeout=30)
            image.raise_for_status()
            return image.content, scene
    raise RuntimeError("AI Gateway non ha restituito un'immagine")


def _font(size, bold=False):
    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _value(player, *keys):
    for key in keys:
        value = player.get(key)
        if value not in (None, ""):
            if key == "brawlers" and isinstance(value, (list, tuple, set, dict)):
                return len(value)
            return value
    return "—"


def _fmt(value):
    return f"{value:,}".replace(",", ".") if isinstance(value, int) else str(value)


def overlay_stats(image_bytes, player, logo_path="assets/titani_logo.jpg"):
    if not os.path.exists(logo_path):
        raise RuntimeError("Logo ufficiale TITANI ABUSIVI non disponibile: immagine non pubblicata.")

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    target_w, target_h = 1080, 1350
    ratio = max(target_w / image.width, target_h / image.height)
    image = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.Resampling.LANCZOS)
    left = (image.width - target_w) // 2
    top = (image.height - target_h) // 2
    image = image.crop((left, top, left + target_w, top + target_h))

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle((45, 45, 1035, 235), radius=34, fill=(5, 8, 18, 190), outline=(255,255,255,90), width=2)
    draw.rounded_rectangle((45, 925, 1035, 1305), radius=34, fill=(5, 8, 18, 205), outline=(255,255,255,90), width=2)
    draw.text((75, 68), str(_value(player,"name")), font=_font(54, True), fill="white")
    draw.text((75, 135), f"{_value(player,'tag')}  •  {_value(player,'club_name','club')}", font=_font(27), fill=(230,230,235,255))

    rows = [
        ("Trofei", _fmt(_value(player,"trophies")), "Brawler", _fmt(_value(player,"brawlers"))),
        ("Livello", _fmt(_value(player,"level")), "Prestigio", _fmt(_value(player,"prestige"))),
        ("Vittorie 3v3", _fmt(_value(player,"wins_3v3")), "Solo / Duo", f"{_fmt(_value(player,'wins_solo'))} / {_fmt(_value(player,'wins_duo'))}"),
        ("Classificata", _fmt(_value(player,"ranked_current")), "Record", _fmt(_value(player,"ranked_career_peak","ranked_peak"))),
    ]
    y = 955
    for l1,v1,l2,v2 in rows:
        draw.text((75,y),l1,font=_font(22),fill=(190,195,210,255))
        draw.text((75,y+28),v1,font=_font(32,True),fill="white")
        draw.text((560,y),l2,font=_font(22),fill=(190,195,210,255))
        draw.text((560,y+28),v2,font=_font(32,True),fill="white")
        y += 82

    logo = Image.open(logo_path).convert("RGBA")
    logo.thumbnail((160,160), Image.Resampling.LANCZOS)
    # Badge leggibile: garantisce che il logo originale sia sempre visibile sul risultato finale.
    badge_x, badge_y = 840, 55
    draw.rounded_rectangle((badge_x-12, badge_y-10, badge_x+logo.width+12, badge_y+logo.height+10), radius=22, fill=(255,255,255,225))
    overlay.alpha_composite(logo, (badge_x, badge_y))

    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    out = io.BytesIO()
    image.save(out, "JPEG", quality=94, optimize=True)
    out.seek(0)
    return out
