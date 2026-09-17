import base64
import io
import os
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from ai_profile_experience import build_visual_prompt, profile_brawler_reference

GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
MONTHLY_LIMIT = int(os.environ.get("AI_PROFILE_MONTHLY_LIMIT", "2"))
PREMIUM_MONTHLY_LIMIT = int(os.environ.get("AI_PROFILE_PREMIUM_MONTHLY_LIMIT", "8"))


def _gemini_key():
    key=os.environ.get("GEMINI_API_KEY")
    if not key: raise RuntimeError("GEMINI_API_KEY non configurata")
    return key


def _supabase_headers():
    key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key: raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY non configurata")
    return {"apikey":key,"Authorization":f"Bearer {key}","Content-Type":"application/json"}


def _month_key(): return datetime.now(timezone.utc).strftime("%Y-%m")


def quota_status(telegram_user_id):
    base=(os.environ.get("SUPABASE_URL") or "").rstrip("/")
    if not base: return {"unlimited":False,"premium":False,"used":0,"base_limit":MONTHLY_LIMIT,"extra":0,"limit":MONTHLY_LIMIT}
    headers=_supabase_headers()
    r=requests.get(f"{base}/rest/v1/ai_profile_privileged_users",params={"telegram_user_id":f"eq.{int(telegram_user_id)}","select":"telegram_user_id","limit":"1"},headers=headers,timeout=10); r.raise_for_status(); premium=bool(r.json())
    r=requests.get(f"{base}/rest/v1/ai_profile_usage",params={"telegram_user_id":f"eq.{int(telegram_user_id)}","month_key":f"eq.{_month_key()}","select":"successful_generations","limit":"1"},headers=headers,timeout=10); r.raise_for_status(); rows=r.json(); used=int(rows[0]["successful_generations"]) if rows else 0
    r=requests.get(f"{base}/rest/v1/ai_profile_quota_bonus",params={"telegram_user_id":f"eq.{int(telegram_user_id)}","select":"permanent_extra","limit":"1"},headers=headers,timeout=10); r.raise_for_status(); rows=r.json(); extra=int(rows[0]["permanent_extra"]) if rows else 0
    base_limit=PREMIUM_MONTHLY_LIMIT if premium else MONTHLY_LIMIT
    return {"unlimited":False,"premium":premium,"used":used,"base_limit":base_limit,"extra":extra,"limit":base_limit+extra}


def consume_quota(telegram_user_id):
    status=quota_status(telegram_user_id); base=(os.environ.get("SUPABASE_URL") or "").rstrip("/"); new=status["used"]+1
    r=requests.post(f"{base}/rest/v1/ai_profile_usage",params={"on_conflict":"telegram_user_id,month_key"},headers={**_supabase_headers(),"Prefer":"resolution=merge-duplicates,return=minimal"},json={"telegram_user_id":int(telegram_user_id),"month_key":_month_key(),"successful_generations":new,"updated_at":datetime.now(timezone.utc).isoformat()},timeout=10); r.raise_for_status(); return {**status,"used":new}


def _download_reference(url):
    if not url: return None,None
    r=requests.get(url,headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0"},timeout=15); r.raise_for_status()
    return r.headers.get("Content-Type","image/png").split(";")[0],base64.b64encode(r.content).decode()


def _local_reference(path):
    if not os.path.exists(path): raise RuntimeError("Logo ufficiale TITANI ABUSIVI non disponibile: generazione annullata.")
    with open(path,"rb") as f: data=f.read()
    ext=os.path.splitext(path)[1].lower(); mime="image/png" if ext==".png" else "image/jpeg"
    return mime,base64.b64encode(data).decode()


def _value(player,*keys):
    for key in keys:
        value=player.get(key)
        if value not in (None,""):
            if key=="brawlers" and isinstance(value,(list,tuple,set,dict)): return len(value)
            return value
    return "—"


def _fmt(v): return f"{v:,}".replace(",",".") if isinstance(v,int) else str(v)


def _profile_data_prompt(player):
    return (f"DATI REALI: Nome {_value(player,'name')}; Tag {_value(player,'tag')}; Club {_value(player,'club_name','club')}; "
            f"Trofei {_fmt(_value(player,'trophies'))}; Livello {_fmt(_value(player,'level'))}; Brawler {_fmt(_value(player,'brawlers'))}; Prestigio {_fmt(_value(player,'prestige'))}; "
            f"Vittorie 3v3 {_fmt(_value(player,'wins_3v3'))}; Solo/Duo {_fmt(_value(player,'wins_solo'))}/{_fmt(_value(player,'wins_duo'))}; Classificata {_fmt(_value(player,'ranked_current'))}; Record {_fmt(_value(player,'ranked_career_peak','ranked_peak'))}. ")


def generate_scene(player,category="random",logo_path="assets/titani_logo.jpg"):
    prompt,scene=build_visual_prompt(player,category); brawler_ref=profile_brawler_reference(player)
    if not brawler_ref: raise RuntimeError("Foto profilo Brawler non disponibile: generazione annullata per non inventare il personaggio.")
    b_mime,b_data=_download_reference(brawler_ref); l_mime,l_data=_local_reference(logo_path)
    character_lock=(
        "CHARACTER LOCK OBBLIGATORIO SULLA PRIMA IMMAGINE. Non reinterpretare il soggetto. Mantieni contorno, proporzioni, occhi, bocca/becco/muso, orecchie/corna/capelli, arti, mani/zampe, costume e accessori della reference. "
        "NON trasformare il Brawler in animale zoologicamente realistico, persona reale, cosplay, mascotte o personaggio ispirato. Deve essere lo STESSO modello del Brawler costruito con materiali fisici reali. "
        "Applica fotorealismo SOLO a superfici, materiali, luce, ombre, profondita e particelle. Non aggiungere anatomia assente. Fedelta reference > fotorealismo > creativita. "
    ) if category=="cinematic" else "La PRIMA immagine allegata e la reference visiva obbligatoria del Brawler: mantieni il personaggio fedele. "
    full_prompt=(prompt+" "+character_lock+
        "La SECONDA immagine allegata e il LOGO ORIGINALE TITANI ABUSIVI: deve comparire riconoscibile e fedele, INTEGRATO FISICAMENTE nella scena, non come watermark o badge. Mantieni forma, simbolo, scritte e identita del logo; non sostituirlo con un logo inventato. Deve essere completamente dentro l'inquadratura. "
        "IMPORTANTE: NON scrivere nell'immagine nome giocatore, tag, club, trofei, livello, numero Brawler, Prestigio, vittorie, Classificata, record o altri valori statistici. Il bot applichera questi dati esatti dopo la generazione. Non duplicare ne inventare statistiche. "
        "Lascia aree visivamente pulite e con contrasto sufficiente nella parte alta e nella parte bassa per la tipografia finale. Non creare box, card, pannelli, HUD, monitor o targhe destinati alle statistiche. Il Brawler resta protagonista. Per icone e marchi ufficiali Brawl Stars non inventare imitazioni: se non disponibili, omettili.")
    payload={"contents":[{"role":"user","parts":[{"text":full_prompt},{"inline_data":{"mime_type":b_mime,"data":b_data}},{"inline_data":{"mime_type":l_mime,"data":l_data}}]}],"generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"imageSize":"1K"}}}
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent"; r=requests.post(url,headers={"x-goog-api-key":_gemini_key(),"Content-Type":"application/json"},json=payload,timeout=180)
    if r.status_code>=400: raise RuntimeError(f"Gemini Image HTTP {r.status_code}: {r.text[:500]}")
    for candidate in r.json().get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline=part.get("inlineData") or part.get("inline_data") or {}; data=inline.get("data"); mime=inline.get("mimeType") or inline.get("mime_type") or ""
            if data and mime.startswith("image/"): return base64.b64decode(data),scene
    raise RuntimeError("Gemini non ha restituito un'immagine")


def _font(size,bold=False):
    paths=["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    for path in paths:
        if os.path.exists(path): return ImageFont.truetype(path,size)
    return ImageFont.load_default()


def _fit_canvas(image,w=1080,h=1350):
    ratio=min(w/image.width,h/image.height)
    fg=image.resize((max(1,int(image.width*ratio)),max(1,int(image.height*ratio))),Image.Resampling.LANCZOS)
    bg=image.resize((w,h),Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    bg.paste(fg,((w-fg.width)//2,(h-fg.height)//2))
    return bg


def _draw_text(draw,xy,text,font,anchor="la"):
    x,y=xy
    draw.text((x,y),str(text),font=font,fill=(255,255,255,255),stroke_width=3,stroke_fill=(10,10,10,235),anchor=anchor)


def overlay_stats(image_bytes,player,logo_path="assets/titani_logo.jpg"):
    """Gemini genera l'arte; i dati reali vengono applicati localmente una sola volta."""
    image=_fit_canvas(Image.open(io.BytesIO(image_bytes)).convert("RGB"),1080,1350).convert("RGBA")
    draw=ImageDraw.Draw(image,"RGBA")
    title=_font(42,True); normal=_font(27,True); small=_font(24,True)
    name=str(_value(player,'name')); tag=str(_value(player,'tag')); club=str(_value(player,'club_name','club'))
    trophies=_fmt(_value(player,'trophies')); level=_fmt(_value(player,'level')); brawlers=_fmt(_value(player,'brawlers')); prestige=_fmt(_value(player,'prestige'))
    wins3=_fmt(_value(player,'wins_3v3')); solo=_fmt(_value(player,'wins_solo')); duo=_fmt(_value(player,'wins_duo')); ranked=_fmt(_value(player,'ranked_current')); record=_fmt(_value(player,'ranked_career_peak','ranked_peak'))
    _draw_text(draw,(55,55),name,title); _draw_text(draw,(56,108),tag,small); _draw_text(draw,(56,145),f"CLUB  {club}",small)
    left=[f"TROFEI  {trophies}",f"LIVELLO  {level}",f"BRAWLER  {brawlers}",f"PRESTIGIO  {prestige}"]
    right=[f"VITTORIE 3v3  {wins3}",f"SOLO  {solo}   DUO  {duo}",f"CLASSIFICATA  {ranked}",f"RECORD  {record}"]
    y=1160
    for row in left: _draw_text(draw,(55,y),row,normal); y+=39
    y=1160
    for row in right: _draw_text(draw,(1025,y),row,normal,"ra"); y+=39
    out=io.BytesIO(); image.convert("RGB").save(out,"JPEG",quality=95,optimize=True); out.seek(0); return out
