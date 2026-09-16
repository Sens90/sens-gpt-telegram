import base64
import io
import os
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageFont

from ai_profile_experience import build_visual_prompt, profile_brawler_reference

GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
MONTHLY_LIMIT = int(os.environ.get("AI_PROFILE_MONTHLY_LIMIT", "2"))
PREMIUM_MONTHLY_LIMIT = int(os.environ.get("AI_PROFILE_PREMIUM_MONTHLY_LIMIT", "8"))


def _gemini_key():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY non configurata")
    return key


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
        return {"unlimited": False, "premium": False, "used": 0, "limit": MONTHLY_LIMIT}
    headers = _supabase_headers()
    privileged = requests.get(
        f"{base}/rest/v1/ai_profile_privileged_users",
        params={"telegram_user_id": f"eq.{int(telegram_user_id)}", "select": "telegram_user_id", "limit": "1"},
        headers=headers, timeout=10,
    )
    privileged.raise_for_status()
    premium = bool(privileged.json())
    usage = requests.get(
        f"{base}/rest/v1/ai_profile_usage",
        params={"telegram_user_id": f"eq.{int(telegram_user_id)}", "month_key": f"eq.{_month_key()}", "select": "successful_generations", "limit": "1"},
        headers=headers, timeout=10,
    )
    usage.raise_for_status()
    rows = usage.json()
    used = int(rows[0]["successful_generations"]) if rows else 0
    return {"unlimited": False, "premium": premium, "used": used, "limit": PREMIUM_MONTHLY_LIMIT if premium else MONTHLY_LIMIT}


def consume_quota(telegram_user_id):
    status = quota_status(telegram_user_id)
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
    return {**status, "used": new_value}


def _reference_image(url):
    if not url:
        return None, None
    response = requests.get(url, headers={"User-Agent": "SensGPT-TitaniAbusivi/1.0"}, timeout=15)
    response.raise_for_status()
    ctype = response.headers.get("Content-Type", "image/png").split(";")[0]
    return ctype, base64.b64encode(response.content).decode()


def generate_scene(player, category="random"):
    prompt, scene = build_visual_prompt(player, category)
    reference = profile_brawler_reference(player)
    if not reference:
        raise RuntimeError("Foto profilo Brawler non disponibile: generazione annullata per non inventare il personaggio.")
    mime_type, image_b64 = _reference_image(reference)
    full_prompt = (
        prompt + " Usa l'immagine allegata come reference VISIVA OBBLIGATORIA del personaggio. "
        "Mantieni il Brawler il piu fedele possibile all'originale. Prevedi nella composizione uno spazio evidente "
        "per il branding TITANI ABUSIVI; il logo ufficiale verra applicato dal bot dopo la generazione. "
        "Non copiare testo o numeri presenti nella reference."
    )
    payload = {
        "contents": [{"role": "user", "parts": [
            {"text": full_prompt},
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
        ]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"imageSize": "1K"},
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent"
    response = requests.post(
        url,
        headers={"x-goog-api-key": _gemini_key(), "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini Image HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    for candidate in data.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            encoded = inline.get("data")
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            if encoded and mime.startswith("image/"):
                return base64.b64decode(encoded), scene
    raise RuntimeError("Gemini non ha restituito un'immagine")


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
        draw.text((75,y),l1,font=_font(22),fill=(190,195,210,255)); draw.text((75,y+28),v1,font=_font(32,True),fill="white")
        draw.text((560,y),l2,font=_font(22),fill=(190,195,210,255)); draw.text((560,y+28),v2,font=_font(32,True),fill="white"); y += 82
    logo = Image.open(logo_path).convert("RGBA"); logo.thumbnail((160,160), Image.Resampling.LANCZOS)
    badge_x, badge_y = 840, 55
    draw.rounded_rectangle((badge_x-12, badge_y-10, badge_x+logo.width+12, badge_y+logo.height+10), radius=22, fill=(255,255,255,225))
    overlay.alpha_composite(logo, (badge_x, badge_y))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    out = io.BytesIO(); image.save(out, "JPEG", quality=94, optimize=True); out.seek(0)
    return out
