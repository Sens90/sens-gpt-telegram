import base64
import io
import os
from datetime import datetime, timezone

import requests
from PIL import Image

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
    return (f"DATI REALI, da riportare senza modificarli: Nome {_value(player,'name')}; Tag {_value(player,'tag')}; Club {_value(player,'club_name','club')}; "
            f"Trofei {_fmt(_value(player,'trophies'))}; Livello {_fmt(_value(player,'level'))}; Brawler {_fmt(_value(player,'brawlers'))}; Prestigio {_fmt(_value(player,'prestige'))}; "
            f"Vittorie 3v3 {_fmt(_value(player,'wins_3v3'))}; Solo/Duo {_fmt(_value(player,'wins_solo'))}/{_fmt(_value(player,'wins_duo'))}; Classificata {_fmt(_value(player,'ranked_current'))}; Record {_fmt(_value(player,'ranked_career_peak','ranked_peak'))}. ")


def generate_scene(player,category="random",logo_path="assets/titani_logo.jpg"):
    prompt,scene=build_visual_prompt(player,category); brawler_ref=profile_brawler_reference(player)
    if not brawler_ref: raise RuntimeError("Foto profilo Brawler non disponibile: generazione annullata per non inventare il personaggio.")
    b_mime,b_data=_download_reference(brawler_ref); l_mime,l_data=_local_reference(logo_path)
    character_lock=(
        "CHARACTER LOCK OBBLIGATORIO SULLA PRIMA IMMAGINE. Non reinterpretare il soggetto. Prima ricostruisci mentalmente la stessa silhouette 2D della reference: contorno esterno, rapporto testa/corpo, dimensione e posizione di occhi, bocca/becco/muso, orecchie/corna/capelli, lunghezza e forma degli arti, mani/zampe, costume e accessori. Questi rapporti devono restare invariati nell'immagine finale. "
        "NON usare l'aspetto della reference per dedurre una specie reale e poi ridisegnarla. NON trasformare il Brawler in un animale zoologicamente realistico, persona reale, cosplay, mascotte o personaggio ispirato. Deve essere chiaramente lo STESSO modello del Brawler, come se il modello originale fosse stato costruito con materiali fisici reali. "
        "Applica il fotorealismo DOPO il character lock e SOLO a superficie e fotografia: microtexture, fibre, piume/pelo dove gia esistono, tessuti, cuciture, metalli, vernice, pelle se prevista dal personaggio, riflessi, illuminazione, ombre, profondita e particelle. Non aggiungere strutture anatomiche assenti nella reference. "
        "Se una scelta aumenta il realismo ma cambia il design, SCARTALA. Fedelta alla prima reference > fotorealismo > creativita. "
    ) if category=="cinematic" else "La PRIMA immagine allegata e la reference visiva obbligatoria del Brawler: mantieni il personaggio fedele. "
    full_prompt=(prompt+" "+character_lock+"La SECONDA immagine allegata e il LOGO ORIGINALE TITANI ABUSIVI: deve comparire riconoscibile e fedele, ma INTEGRATO FISICAMENTE NEL CONTESTO DELLA SCENA, non appoggiato come watermark o badge. In base all'ambientazione trasformalo in modo naturale in un'insegna, bandiera, stendardo, incisione, graffiti, ologramma, schermo, stemma su muro/edificio, tessuto o altro elemento scenografico coerente. Mantieni forma, simbolo, scritte e identita del logo originale; non sostituirlo con un logo inventato. Deve essere ben visibile e COMPLETAMENTE dentro l'inquadratura, con margine dai bordi. "+_profile_data_prompt(player)+" Nome, tag, club e statistiche devono essere integrati fisicamente nella scenografia, senza box, card, pannelli, targhette traslucide, cornici o HUD sospesi. Mantieni TUTTI i testi completamente dentro una safe area interna, lontani dai quattro bordi, leggibili e senza tagli. Il Brawler resta protagonista. Per icone e marchi ufficiali Brawl Stars non inventare imitazioni: usa solo riferimenti ufficiali disponibili; se non disponibili, ometti l'icona.")
    payload={"contents":[{"role":"user","parts":[{"text":full_prompt},{"inline_data":{"mime_type":b_mime,"data":b_data}},{"inline_data":{"mime_type":l_mime,"data":l_data}}]}],"generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"imageSize":"1K"}}}
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent"; r=requests.post(url,headers={"x-goog-api-key":_gemini_key(),"Content-Type":"application/json"},json=payload,timeout=180)
    if r.status_code>=400: raise RuntimeError(f"Gemini Image HTTP {r.status_code}: {r.text[:500]}")
    for candidate in r.json().get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline=part.get("inlineData") or part.get("inline_data") or {}; data=inline.get("data"); mime=inline.get("mimeType") or inline.get("mime_type") or ""
            if data and mime.startswith("image/"): return base64.b64decode(data),scene
    raise RuntimeError("Gemini non ha restituito un'immagine")


def overlay_stats(image_bytes,player,logo_path="assets/titani_logo.jpg"):
    """Solo ritaglio finale: logo, nome e statistiche sono gia integrati da Gemini nella scena."""
    image=Image.open(io.BytesIO(image_bytes)).convert("RGB"); w,h=1080,1350; ratio=max(w/image.width,h/image.height); image=image.resize((int(image.width*ratio),int(image.height*ratio)),Image.Resampling.LANCZOS); left=(image.width-w)//2; top=(image.height-h)//2; image=image.crop((left,top,left+w,top+h)); out=io.BytesIO(); image.save(out,"JPEG",quality=94,optimize=True); out.seek(0); return out
