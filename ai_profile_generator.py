import base64
import io
import os
from datetime import datetime, timezone

import requests

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


def _club_value(player):
    club_name=player.get("club_name")
    club=player.get("club")
    if isinstance(club,dict): club=club.get("name")
    for value in (club_name,club):
        if value and str(value).strip() not in {"—","-","Senza club","Senza club / non disponibile"}:
            return str(value).split("\n",1)[0].strip()
    base=(os.environ.get("SUPABASE_URL") or "").rstrip("/")
    tag=str(player.get("tag") or "").upper().replace("#","").strip()
    if base and tag:
        try:
            r=requests.get(f"{base}/rest/v1/community_members",params={"player_tag":f"eq.{tag}","select":"*","limit":"1"},headers=_supabase_headers(),timeout=8)
            r.raise_for_status(); rows=r.json()
            if rows:
                for key in ("club_name","club"):
                    value=rows[0].get(key)
                    if value and str(value).strip() not in {"—","-","Senza club","Senza club / non disponibile"}:
                        return str(value).split("\n",1)[0].strip()
        except Exception as exc:
            print("AI PROFILE CLUB FALLBACK:",repr(exc),flush=True)
    return "—"


def _profile_data_prompt(player):
    rows=[
        ("NOME",_value(player,"name")),
        ("TAG",_value(player,"tag")),
        ("CLUB",_club_value(player)),
        ("TROFEI",_fmt(_value(player,"trophies"))),
        ("LIVELLO",_fmt(_value(player,"level"))),
        ("BRAWLER",_fmt(_value(player,"brawlers"))),
        ("PRESTIGIO",_fmt(_value(player,"prestige"))),
        ("VITTORIE 3v3",_fmt(_value(player,"wins_3v3"))),
        ("SOLO",_fmt(_value(player,"wins_solo"))),
        ("DUO",_fmt(_value(player,"wins_duo"))),
        ("CLASSIFICATA",_fmt(_value(player,"ranked_current"))),
        ("RECORD",_fmt(_value(player,"ranked_career_peak","ranked_peak"))),
    ]
    return "\n".join(f"{label}: {value}" for label,value in rows)


def generate_scene(player,category="random",logo_path="assets/titani_logo.jpg"):
    prompt,scene=build_visual_prompt(player,category); brawler_ref=profile_brawler_reference(player)
    if not brawler_ref: raise RuntimeError("Foto profilo Brawler non disponibile: generazione annullata per non inventare il personaggio.")
    b_mime,b_data=_download_reference(brawler_ref); l_mime,l_data=_local_reference(logo_path)
    character_lock=(
        "CHARACTER LOCK ASSOLUTO: la PRIMA immagine allegata e la reference canonica e VINCOLANTE del Brawler. Riproduci lo STESSO personaggio, non una reinterpretazione. "
        "Prima di generare, osserva e conserva TUTTI gli elementi visibili nella reference: silhouette, proporzioni, testa, corpo, arti, costume, copricapo/elmetto/cappello, capelli, barba, guanti, scarpe, armi, strumenti, zaino, accessori, colori, simboli e tratti del volto. "
        "NESSUN elemento visibile nella reference puo essere rimosso, sostituito o ridisegnato. In particolare, se il Brawler indossa un ELMETTO o altro copricapo nella reference, deve indossare ESATTAMENTE quel copricapo anche nell'immagine finale, con forma e colori coerenti. "
        "Non inventare anatomia o tratti facciali assenti. Non trasformare il Brawler in umano, animale, cosplay o mascotte. La posa puo cambiare, il character design NO. "
        "Lo stile richiesto puo modificare soltanto rendering, materiali, texture, luce, profondita e atmosfera. FEDELTA ALLA REFERENCE > STILE > CREATIVITA. "
    )
    if category=="cinematic": character_lock += "Cinematic: fotorealismo solo nei materiali e nella fotografia; character design invariato. "
    elif category=="pixar": character_lock += "Pixar: hyper detailed 3D cinematic animation e high fidelity render, ma nessuna modifica al character design o al volto. "

    data=_profile_data_prompt(player)
    data_lock=(
        "Crea una LOCANDINA PROFILO COMPLETA: Nano Banana deve generare DIRETTAMENTE nell'immagine tutta la tipografia e tutti i dati del giocatore, integrandoli artisticamente e naturalmente nell'ambientazione (per esempio cartelli, insegne, pietra, monitor, targhe, pannelli o elementi scenografici coerenti). "
        "NON lasciare bande nere o aree predisposte per un overlay successivo. NON usare il vecchio layout con testo bianco sovrapposto in alto o in basso. La composizione deve sembrare un'unica opera grafica generata. "
        "I seguenti dati sono DATI REALI BLOCCATI. Trascrivili ESATTAMENTE, carattere per carattere. Non correggere, reinterpretare, abbreviare, tradurre, arrotondare o sostituire alcun valore. Non inventare statistiche aggiuntive e non ripetere valori diversi in altri punti.\n"
        "--- DATI OBBLIGATORI ---\n"+data+"\n--- FINE DATI ---\n"
        "Devono essere tutti chiaramente leggibili e presenti UNA SOLA VOLTA. Dai priorita assoluta alla correttezza di lettere e cifre rispetto alle decorazioni. "
    )
    logo_lock=(
        "LOGO LOCK ASSOLUTO: la SECONDA immagine allegata NON e una semplice ispirazione: e l'asset grafico ufficiale e VINCOLANTE dei TITANI ABUSIVI. "
        "Devi riprodurre QUELLO STESSO LOGO, mantenendo identita, geometria, composizione, proporzioni relative, scudo, simbolo/maschera centrale, elementi viola e oro, alloro/ornamenti e lettering TITANI ABUSIVI come visibili nella reference. "
        "NON creare un nuovo stemma. NON sostituire la maschera centrale con leone, tigre, gufo, teschio, corona, Brawler o altro simbolo. NON cambiare il disegno dello scudo. NON cambiare o reinventare la scritta. NON aggiungere elementi dentro il logo. "
        "Il logo puo essere adattato SOLTANTO per prospettiva, illuminazione, ombre, materiale e integrazione fisica nella scena; la sua IDENTITA VISIVA deve restare invariata. "
        "Prima di finalizzare l'immagine, confronta mentalmente il logo generato con la SECONDA reference: se stemma, maschera, alloro, colori o lettering non corrispondono, correggili prima dell'output. "
        "PRIORITA LOGO: FEDELTA ALLA SECONDA REFERENCE > INTEGRAZIONE NELLA SCENA > CREATIVITA. Mostra il logo ufficiale una sola volta, abbastanza grande e nitido da essere riconoscibile. "
    )
    full_prompt=prompt+" "+character_lock+logo_lock+data_lock
    payload={"contents":[{"role":"user","parts":[{"text":full_prompt},{"inline_data":{"mime_type":b_mime,"data":b_data}},{"inline_data":{"mime_type":l_mime,"data":l_data}}]}],"generationConfig":{"responseModalities":["TEXT","IMAGE"],"imageConfig":{"imageSize":"1K"}}}
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent"; r=requests.post(url,headers={"x-goog-api-key":_gemini_key(),"Content-Type":"application/json"},json=payload,timeout=180)
    if r.status_code>=400: raise RuntimeError(f"Gemini Image HTTP {r.status_code}: {r.text[:500]}")
    for candidate in r.json().get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline=part.get("inlineData") or part.get("inline_data") or {}; encoded=inline.get("data"); mime=inline.get("mimeType") or inline.get("mime_type") or ""
            if encoded and mime.startswith("image/"): return base64.b64decode(encoded),scene
    raise RuntimeError("Gemini non ha restituito un'immagine")


def overlay_stats(image_bytes,player,logo_path="assets/titani_logo.jpg"):
    """Compatibility hook: Nano Banana now renders the complete profile itself."""
    out=io.BytesIO(image_bytes); out.seek(0); out.name="profilo_ai.jpg"; return out
