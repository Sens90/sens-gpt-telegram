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
    r=requests.get(f"{base}/rest/v1/ai_profile_quota_bonus",params={"telegram_user_id":f"eq.{int(telegram_user_id)}","select":"permanent_extra,updated_at","limit":"1"},headers=headers,timeout=10); r.raise_for_status(); rows=r.json()
    extra=0
    if rows and str(rows[0].get("updated_at") or "")[:7]==_month_key(): extra=int(rows[0].get("permanent_extra") or 0)
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


def _club_value(player):
    club_name=player.get("club_name"); club=player.get("club")
    if isinstance(club,dict): club=club.get("name")
    for value in (club_name,club):
        if value and str(value).strip() not in {"—","-","Senza club","Senza club / non disponibile"}: return str(value).split("\n",1)[0].strip()
    base=(os.environ.get("SUPABASE_URL") or "").rstrip("/"); tag=str(player.get("tag") or "").upper().replace("#","").strip()
    if base and tag:
        try:
            r=requests.get(f"{base}/rest/v1/community_members",params={"player_tag":f"eq.{tag}","select":"*","limit":"1"},headers=_supabase_headers(),timeout=8); r.raise_for_status(); rows=r.json()
            if rows:
                for key in ("club_name","club"):
                    value=rows[0].get(key)
                    if value and str(value).strip() not in {"—","-","Senza club","Senza club / non disponibile"}: return str(value).split("\n",1)[0].strip()
        except Exception as exc: print("AI PROFILE CLUB FALLBACK:",repr(exc),flush=True)
    return "—"


def _profile_data_prompt(player):
    rows=[("NOME",_value(player,"name")),("TAG",_value(player,"tag")),("CLUB",_club_value(player)),("TROFEI",_fmt(_value(player,"trophies"))),("LIVELLO",_fmt(_value(player,"level"))),("BRAWLER",_fmt(_value(player,"brawlers"))),("PRESTIGIO",_fmt(_value(player,"prestige"))),("VITTORIE 3v3",_fmt(_value(player,"wins_3v3"))),("SOLO",_fmt(_value(player,"wins_solo"))),("DUO",_fmt(_value(player,"wins_duo"))),("CLASSIFICATA",_fmt(_value(player,"ranked_current"))),("RECORD",_fmt(_value(player,"ranked_career_peak","ranked_peak")))]
    return "\n".join(f"{label}: {value}" for label,value in rows)


def generate_scene(player,category="random",logo_path="assets/titani_logo.jpg"):
    prompt,scene=build_visual_prompt(player,category); brawler_ref=profile_brawler_reference(player)
    if not brawler_ref: raise RuntimeError("Foto profilo Brawler non disponibile: generazione annullata per non inventare il personaggio.")
    b_mime,b_data=_download_reference(brawler_ref); l_mime,l_data=_local_reference(logo_path)
    character_lock=("CHARACTER LOCK ASSOLUTO: la PRIMA immagine allegata e la reference canonica e VINCOLANTE del Brawler. Riproduci lo STESSO personaggio, non una reinterpretazione. Prima di generare, osserva e conserva TUTTI gli elementi visibili nella reference: silhouette, proporzioni, testa, corpo, arti, costume, copricapo/elmetto/cappello, capelli, barba, guanti, scarpe, armi, strumenti, zaino, accessori, colori, simboli e tratti del volto. NESSUN elemento visibile nella reference puo essere rimosso, sostituito o ridisegnato. In particolare, se il Brawler indossa un ELMETTO o altro copricapo nella reference, deve indossare ESATTAMENTE quel copricapo anche nell'immagine finale, con forma e colori coerenti. Non inventare anatomia o tratti facciali assenti. Non trasformare il Brawler in umano, animale, cosplay o mascotte. La posa puo cambiare, il character design NO. ")
    if player.get("profile_skin"): character_lock+=f"SKIN LOCK: la reference mostra la skin '{player['profile_skin']}' di {player.get('profile_brawler')}. Mantieni esattamente questa skin; non tornare alla skin base e non mescolare elementi di altre skin. "
    # Gemini must create only the scene. Exact logo and profile data are composited afterwards.
    scene_lock=("SCENA PULITA: NON generare statistiche, nomi, tag, numeri, loghi, stemmi, watermark, insegne testuali o pannelli UI. "
                "Lascia spazio visivo libero nella parte inferiore e in alto a destra per il compositing deterministico. ")
    full_prompt=character_lock+scene_lock+prompt
    payload={"contents":[{"parts":[{"text":full_prompt},{"inline_data":{"mime_type":b_mime,"data":b_data}},{"inline_data":{"mime_type":l_mime,"data":l_data}}]}],"generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"aspectRatio":"1:1","imageSize":"1K"}}}
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent?key={_gemini_key()}"; r=requests.post(url,json=payload,timeout=120)
    if r.status_code>=400: raise RuntimeError(f"Gemini image HTTP {r.status_code}: {r.text[:500]}")
    data=r.json()
    for candidate in data.get("candidates",[]):
        for part in candidate.get("content",{}).get("parts",[]):
            inline=part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"): return base64.b64decode(inline["data"]),scene
    raise RuntimeError("Gemini non ha restituito un'immagine")


def overlay_stats(image_bytes,player,logo_path="assets/titani_logo.jpg"):
    image=Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    w,h=image.size
    draw=ImageDraw.Draw(image,"RGBA")
    def font(size,bold=False):
        paths=["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        for p in paths:
            try:return ImageFont.truetype(p,max(12,int(size)))
            except Exception:pass
        return ImageFont.load_default()
    # Exact official logo: composite the original asset, never redraw it with AI.
    if os.path.exists(logo_path):
        logo=Image.open(logo_path).convert("RGBA")
        target=max(96,int(w*.18)); logo.thumbnail((target,target),Image.Resampling.LANCZOS)
        image.alpha_composite(logo,(w-logo.width-int(w*.035),int(h*.035)))
    panel_h=int(h*.36); y0=h-panel_h
    draw.rounded_rectangle((int(w*.035),y0,int(w*.965),int(h*.965)),radius=max(12,int(w*.025)),fill=(0,0,0,190))
    title=f"{_value(player,'name')}   {_value(player,'tag')}"
    draw.text((int(w*.065),y0+int(panel_h*.10)),title,font=font(w*.034,True),fill=(255,255,255,255))
    club=_club_value(player)
    draw.text((int(w*.065),y0+int(panel_h*.29)),f"CLUB  {club}",font=font(w*.022,True),fill=(255,255,255,255))
    rows=[
      (f"TROFEI  {_fmt(_value(player,'trophies'))}",f"BRAWLER  {_fmt(_value(player,'brawlers'))}   LIVELLO  {_fmt(_value(player,'level'))}"),
      (f"PRESTIGIO  {_fmt(_value(player,'prestige'))}",f"3v3  {_fmt(_value(player,'wins_3v3'))}"),
      (f"SOLO  {_fmt(_value(player,'wins_solo'))}   DUO  {_fmt(_value(player,'wins_duo'))}",f"CLASSIFICATA  {_fmt(_value(player,'ranked_current'))}"),
      (f"RECORD STAGIONE  {_fmt(_value(player,'ranked_season_peak'))}",f"RECORD CARRIERA  {_fmt(_value(player,'ranked_career_peak','ranked_peak'))}")
    ]
    yy=y0+int(panel_h*.46)
    for left,right in rows:
        draw.text((int(w*.065),yy),left,font=font(w*.020,True),fill=(255,255,255,255))
        draw.text((int(w*.53),yy),right,font=font(w*.018,True),fill=(255,255,255,255))
        yy+=int(panel_h*.135)
    out=io.BytesIO(); image.convert("RGB").save(out,"JPEG",quality=94,optimize=True); out.seek(0)
    return out,"profilo_ai.jpg"
