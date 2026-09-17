import html
import os
import random
import re
import unicodedata
from urllib.parse import urljoin

import requests

BRAWLERS_URL = "https://api.brawlapi.com/v1/brawlers"
ICONS_URL = "https://api.brawlapi.com/v1/icons"
BRAWLZONE_PLAYER_URL = "https://brawlzone.net/player/{tag}"
_brawlers_cache = None
_icons_cache = None
BRAWLER_ALIASES_IT = {
    "bombardino":"Berry","corvo":"Crow","dinamike":"Dynamike","dinamite":"Dynamike",
    "elprimo":"El Primo","franco":"Frank","grommo":"Grom","leone":"Leon","mortisio":"Mortis",
    "signorp":"Mr. P","misterp":"Mr. P","otto bit":"8-Bit","ottobit":"8-Bit","otto-bit":"8-Bit",
    "spina":"Spike","ringhio":"Ruffs","colonnello":"Ruffs","colonnello ringhio":"Ruffs",
    "colonnelloringhio":"Ruffs","ambra":"Amber","stecca":"Rico","eugenio":"Gene","pocho":"Poco",
    "barryl":"Darryl","semino":"Sprout","energetik":"Surge","gelindo":"Gale","maxine":"Max",
    "iris":"Nani"
}


def _norm(value):
    value=unicodedata.normalize("NFKD",str(value or "")); value="".join(ch for ch in value if not unicodedata.combining(ch)).casefold(); return re.sub(r"[^a-z0-9]+","",value)


def _catalog(timeout=12):
    global _brawlers_cache,_icons_cache
    if _brawlers_cache is None:
        r=requests.get(BRAWLERS_URL,timeout=timeout); r.raise_for_status(); _brawlers_cache=(r.json() or {}).get("list",[])
    if _icons_cache is None:
        r=requests.get(ICONS_URL,timeout=timeout); r.raise_for_status(); _icons_cache=(r.json() or {}).get("player",{})
    return _brawlers_cache,_icons_cache


def _brawler_by_name(name,brawlers):
    wanted=_norm(name)
    if not wanted:return None
    for item in brawlers:
        if wanted in {_norm(item.get("name")),_norm(item.get("path")),_norm(item.get("hash"))}:return item
    aliases={_norm(a):c for a,c in BRAWLER_ALIASES_IT.items()}; canonical=aliases.get(wanted)
    if canonical:
        for item in brawlers:
            if _norm(canonical)==_norm(item.get("name")):return item
    return None


def _brawler_by_id(brawler_id,brawlers):
    try: target=int(brawler_id)
    except (TypeError,ValueError): return None
    for item in brawlers:
        if item.get("id")==target:return item
    return None


def _apply_brawler(player,brawler):
    if not brawler:return False
    image=brawler.get("imageUrl2") or brawler.get("imageUrl") or brawler.get("imageUrl3")
    if not image:return False
    player["profile_brawler"]=brawler.get("name"); player["profile_brawler_id"]=brawler.get("id"); player["profile_brawler_image_url"]=image; player.pop("profile_skin",None); return True


def _icon_id_from_brawlzone(tag,timeout=15):
    clean=str(tag or "").upper().replace("#","").strip()
    if not clean:return None
    r=requests.get(BRAWLZONE_PLAYER_URL.format(tag=clean),headers={"User-Agent":"Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)"},timeout=timeout)
    if r.status_code!=200:return None
    for pattern in (r'"icon"\s*:\s*\{[^{}]{0,250}?"id"\s*:\s*(\d+)',r'\\"icon\\"\s*:\s*\{[^{}]{0,250}?\\"id\\"\s*:\s*(\d+)',r'"iconId"\s*:\s*(\d+)',r'\\"iconId\\"\s*:\s*(\d+)',r'profile-icons/(?:regular|borderless)/(\d+)\.(?:png|webp)'):
        m=re.search(pattern,r.text,re.I|re.S)
        if m:return int(m.group(1))
    return None


def _supabase_headers():
    key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:return None
    return {"apikey":key,"Authorization":f"Bearer {key}","Accept":"application/json"}


def _verified_skin_reference(brawler,skin_name,timeout=12):
    """Return only a centrally verified skin reference. Never guess an image."""
    base=(os.environ.get("SUPABASE_URL") or "").rstrip("/"); headers=_supabase_headers()
    if not base or not headers:return None,None
    try:
        r=requests.get(
            f"{base}/rest/v1/skins_catalog",
            params={"brawler_id":f"eq.{int(brawler.get('id'))}","verification_status":"eq.verified","image_verified":"eq.true","select":"name_en,name_it,image_url,brawler_name","limit":"500"},
            headers=headers,timeout=timeout,
        ); r.raise_for_status(); rows=r.json() or []
    except Exception:return None,None
    wanted=_norm(skin_name)
    exact=[]
    for row in rows:
        names=[row.get("name_it"),row.get("name_en")]
        forms=set()
        for name in names:
            n=_norm(name)
            if not n:continue
            forms.add(n)
            bnorm=_norm(brawler.get("name")); it_bnorms=[_norm(x) for x,c in BRAWLER_ALIASES_IT.items() if _norm(c)==bnorm]
            for prefix in [bnorm]+it_bnorms:
                if n.startswith(prefix) and len(n)>len(prefix):forms.add(n[len(prefix):])
                if n.endswith(prefix) and len(n)>len(prefix):forms.add(n[:-len(prefix)])
        if wanted in forms and row.get("image_url"):exact.append(row)
    if len(exact)!=1:return None,None
    row=exact[0]
    return row.get("image_url"),row.get("name_it") or row.get("name_en")


def resolve_ai_brawler_reference(player,requested_brawler=None,timeout=15):
    try:
        brawlers,icons=_catalog(timeout=timeout)
        if requested_brawler:
            requested=str(requested_brawler).strip(); skin_name=None
            env_match=re.match(r"^(.+?)\s+ambientazione\s+(.+)$",requested,re.I)
            if env_match:requested=env_match.group(1).strip(); player["ai_custom_environment"]=env_match.group(2).strip()[:160]
            natural_skin=re.match(r"^skin\s+di\s+(.+)$",requested,re.I)
            if natural_skin:
                rest=natural_skin.group(1).strip()
                candidates=sorted(list(BRAWLER_ALIASES_IT)+[str(x.get('name') or '') for x in brawlers],key=len,reverse=True)
                for candidate in candidates:
                    if _norm(rest).startswith(_norm(candidate)):
                        pattern=r"^"+r"\s*".join(re.escape(x) for x in candidate.split())+r"\s+(.+)$"; m=re.match(pattern,rest,re.I)
                        if m and _brawler_by_name(candidate,brawlers):requested=candidate; skin_name=m.group(1).strip(); break
            skin_match=re.match(r"^(.+?)\s+skin(?:\s+di)?\s+(.+)$",requested,re.I)
            if skin_match:requested=skin_match.group(1).strip(); skin_name=skin_match.group(2).strip()
            brawler=_brawler_by_name(requested,brawlers)
            if not brawler:return False,f"Brawler '{requested}' non trovato. Usa il nome italiano o quello ufficiale del Brawler."
            if skin_name:
                skin_url,resolved_name=_verified_skin_reference(brawler,skin_name,timeout=timeout)
                if not skin_url:return False,f"Skin '{skin_name}' di {brawler.get('name')} non ancora verificata nel catalogo TITANI ABUSIVI. Generazione annullata e quota non consumata."
                player["profile_brawler"]=brawler.get("name"); player["profile_brawler_id"]=brawler.get("id"); player["profile_brawler_image_url"]=skin_url; player["profile_skin"]=resolved_name or skin_name; return True,None
            return (_apply_brawler(player,brawler),None)
        icon_id=player.get("icon_id")
        if icon_id is None:
            icon_id=_icon_id_from_brawlzone(player.get("tag"),timeout=timeout)
            if icon_id is not None:player["icon_id"]=icon_id
        icon=icons.get(str(icon_id)) if icon_id is not None else None
        if icon:
            player["profile_icon_url"]=icon.get("imageUrl2") or icon.get("imageUrl"); brawler=_brawler_by_id(icon.get("brawler"),brawlers)
            if _apply_brawler(player,brawler):return True,None
        return False,"Non riesco a collegare la tua icona profilo a un Brawler. Usa: profilo ai cinematic #TAG con NOME_BRAWLER"
    except Exception as exc:return False,f"Reference Brawler non disponibile ({type(exc).__name__}). Riprova tra poco."
