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
        return {"unlimited": False, "premium": False, "used": 0, "base_limit": MONTHLY_LIMIT, "extra": 0, "limit": MONTHLY_LIMIT}
    headers = _supabase_headers()
    privileged = requests.get(f"{base}/rest/v1/ai_profile_privileged_users", params={"telegram_user_id": f"eq.{int(telegram_user_id)}", "select": "telegram_user_id", "limit": "1"}, headers=headers, timeout=10)
    privileged.raise_for_status(); premium = bool(privileged.json())
    usage = requests.get(f"{base}/rest/v1/ai_profile_usage", params={"telegram_user_id": f"eq.{int(telegram_user_id)}", "month_key": f"eq.{_month_key()}", "select": "successful_generations", "limit": "1"}, headers=headers, timeout=10)
    usage.raise_for_status(); rows = usage.json(); used = int(rows[0]["successful_generations"]) if rows else 0
    bonus = requests.get(f"{base}/rest/v1/ai_profile_quota_bonus", params={"telegram_user_id": f"eq.{int(telegram_user_id)}", "select": "permanent_extra", "limit": "1"}, headers=headers, timeout=10)
    bonus.raise_for_status(); bonus_rows = bonus.json(); extra = int(bonus_rows[0]["permanent_extra"]) if bonus_rows else 0
    base_limit = PREMIUM_MONTHLY_LIMIT if premium else MONTHLY_LIMIT
    return {"unlimited": False, "premium": premium, "used": used, "base_limit": base_limit, "extra": extra, "limit": base_limit + extra}


def consume_quota(telegram_user_id):
    status = quota_status(telegram_user_id); base = (os.environ.get("SUPABASE_URL") or "").rstrip("/"); headers = _supabase_headers(); new_value = status["used"] + 1
    response = requests.post(f"{base}/rest/v1/ai_profile_usage", params={"on_conflict": "telegram_user_id,month_key"}, headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"}, json={"telegram_user_id": int(telegram_user_id), "month_key": _month_key(), "successful_generations": new_value, "updated_at": datetime.now(timezone.utc).isoformat()}, timeout=10)
    response.raise_for_status(); return {**status, "used": new_value}


def _reference_image(url):
    if not url: return None, None
    response = requests.get(url, headers={"User-Agent": "SensGPT-TitaniAbusivi/1.0"}, timeout=15); response.raise_for_status()
    return response.headers.get("Content-Type", "image/png").split(";")[0], base64.b64encode(response.content).decode()


def _value(player, *keys):
    for key in keys:
        value = player.get(key)
        if value not in (None, ""):
            if key == "brawlers" and isinstance(value, (list, tuple, set, dict)): return len(value)
            return value
    return "—"


def _fmt(value):
    return f"{value:,}".replace(",", ".") if isinstance(value, int) else str(value)


def _profile_data_prompt(player):
    return (
        "DATI REALI DA RAPPRESENTARE VISIVAMENTE NEL POSTER, senza inventare o modificare i valori: "
        f"Nome: {_value(player,'name')}; Tag: {_value(player,'tag')}; Club: {_value(player,'club_name','club')}; "
        f"Trofei: {_fmt(_value(player,'trophies'))}; Livello: {_fmt(_value(player,'level'))}; "
        f"Brawler: {_fmt(_value(player,'brawlers'))}; Prestigio: {_fmt(_value(player,'prestige'))}; "
        f"Vittorie 3v3: {_fmt(_value(player,'wins_3v3'))}; Solo/Duo: {_fmt(_value(player,'wins_solo'))}/{_fmt(_value(player,'wins_duo'))}; "
        f"Classificata: {_fmt(_value(player,'ranked_current'))}; Record: {_fmt(_value(player,'ranked_career_peak','ranked_peak'))}. "
    )


def generate_scene(player, category="random"):
    prompt, scene = build_visual_prompt(player, category); reference = profile_brawler_reference(player)
    if not reference: raise RuntimeError("Foto profilo Brawler non disponibile: generazione annullata per non inventare il personaggio.")
    mime_type, image_b64 = _reference_image(reference)
    full_prompt = (
        prompt + " Usa l'immagine allegata come reference VISIVA OBBLIGATORIA del Brawler e mantienilo fedele all'originale. "
        + _profile_data_prompt(player) +
        "STILE PROFILO: crea un unico poster cinematografico verticale, ricco e dinamico. Nome, tag, club e statistiche devono sembrare elementi nativi della scena: scritte su insegne, pietra, metallo, banner, elementi HUD leggeri o tipografia ambientale. NON creare grandi riquadri, card, pannelli neri, cornici o box separati sopra e sotto l'immagine. Il Brawler deve restare protagonista e leggibile. "
        "Per simboli e marchi di Brawl Stars usa esclusivamente elementi visivi fedeli agli asset ufficiali del gioco presenti nella reference o forniti dal bot; non inventare, ridisegnare o sostituire loghi ufficiali con imitazioni. Se un'icona ufficiale non e disponibile, lascia spazio pulito invece di crearne una falsa. "
        "Non generare il logo TITANI ABUSIVI: lascia una zona naturale e visibile perche il bot applichera dopo il file originale del logo. Non duplicare il branding TITANI ABUSIVI."
    )
    payload={"contents":[{"role":"user","parts":[{"text":full_prompt},{"inline_data":{"mime_type":mime_type,"data":image_b64}}]}],"generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"imageSize":"1K"}}}
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent"
    response=requests.post(url,headers={"x-goog-api-key":_gemini_key(),"Content-Type":"application/json"},json=payload,timeout=180)
    if response.status_code>=400: raise RuntimeError(f"Gemini Image HTTP {response.status_code}: {response.text[:500]}")
    data=response.json()
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline=part.get("inlineData") or part.get("inline_data") or {}; encoded=inline.get("data"); mime=inline.get("mimeType") or inline.get("mime_type") or ""
            if encoded and mime.startswith("image/"): return base64.b64decode(encoded), scene
    raise RuntimeError("Gemini non ha restituito un'immagine")


def overlay_stats(image_bytes, player, logo_path="assets/titani_logo.jpg"):
    """Finalizza il poster senza card statistiche: applica solo il logo originale del club.

    Nome, tag e statistiche vengono ora richiesti a Gemini come parte della composizione.
    Il logo del club resta applicato dal bot da file originale per garantirne la fedelta.
    """
    if not os.path.exists(logo_path): raise RuntimeError("Logo ufficiale TITANI ABUSIVI non disponibile: immagine non pubblicata.")
    image=Image.open(io.BytesIO(image_bytes)).convert("RGB"); target_w,target_h=1080,1350
    ratio=max(target_w/image.width,target_h/image.height); image=image.resize((int(image.width*ratio),int(image.height*ratio)),Image.Resampling.LANCZOS)
    left=(image.width-target_w)//2; top=(image.height-target_h)//2; image=image.crop((left,top,left+target_w,top+target_h)).convert("RGBA")
    logo=Image.open(logo_path).convert("RGBA"); logo.thumbnail((150,150),Image.Resampling.LANCZOS)
    # Piccolo marchio originale integrato nell'angolo, senza riquadro bianco o pannello.
    shadow=Image.new("RGBA",image.size,(0,0,0,0)); sd=ImageDraw.Draw(shadow); x=target_w-logo.width-38; y=34
    sd.rounded_rectangle((x-10,y-10,x+logo.width+10,y+logo.height+10),radius=18,fill=(0,0,0,95)); image=Image.alpha_composite(image,shadow); image.alpha_composite(logo,(x,y))
    out=io.BytesIO(); image.convert("RGB").save(out,"JPEG",quality=94,optimize=True); out.seek(0); return out
