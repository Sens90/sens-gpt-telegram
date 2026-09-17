import html
import random
import re
import unicodedata
from urllib.parse import urljoin

import requests

BRAWLERS_URL = "https://api.brawlapi.com/v1/brawlers"
ICONS_URL = "https://api.brawlapi.com/v1/icons"
BRAWLZONE_PLAYER_URL = "https://brawlzone.net/player/{tag}"
BRAWLIFY_BRAWLER_URL = "https://brawlify.com/player/{tag}/brawlers/{brawler_id}"
BRAWLVALUE_SKINS_URL = "https://brawlvalue.com/skins/{brawler_slug}"
_brawlers_cache = None
_icons_cache = None
BRAWLER_ALIASES_IT = {"bombardino":"Berry","corvo":"Crow","dinamike":"Dynamike","dinamite":"Dynamike","elprimo":"El Primo","franco":"Frank","grommo":"Grom","leone":"Leon","mortisio":"Mortis","ringhio":"Ruffs","colonnello ringhio":"Ruffs","colonnelloringhio":"Ruffs","signorp":"Mr. P","misterp":"Mr. P","otto bit":"8-Bit","ottobit":"8-Bit","otto-bit":"8-Bit","spina":"Spike"}


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


def _extract_image_urls(fragment,base_url):
    urls=[]
    decoded=html.unescape(fragment).replace("\\u002F","/").replace("\\/","/")
    patterns=(
        r'''(?:src|data-src|imageUrl|image_url)["']?\s*[:=]\s*["']([^"']+)''',
        r'''(https?://[^"'<>\s]+\.(?:png|webp|jpg|jpeg)(?:\?[^"'<>\s]*)?)''',
    )
    for pattern in patterns:
        for raw in re.findall(pattern,decoded,re.I):
            url=urljoin(base_url,raw)
            if url.startswith("http") and url not in urls: urls.append(url)
    return urls


def _is_skin_image(url,timeout=10):
    try:
        r=requests.get(url,headers={"User-Agent":"Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)"},timeout=timeout,stream=True)
        return r.status_code==200 and (r.headers.get("Content-Type") or "").lower().startswith("image/")
    except Exception:
        return False


def _italian_skin_reference(brawler,skin_name,timeout=15):
    """Resolve skins from an Italian, brawler-wide catalog, independent of player ownership."""
    slug=_norm(brawler.get("name"))
    if not slug:return None,None
    page_url=BRAWLVALUE_SKINS_URL.format(brawler_slug=slug)
    try:
        r=requests.get(page_url,headers={"User-Agent":"Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)","Accept-Language":"it-IT,it;q=0.9"},timeout=timeout)
        if r.status_code!=200:return None,None
    except Exception:
        return None,None
    text=html.unescape(r.text)
    requested=_norm(skin_name)
    brawler_norm=_norm(brawler.get("name"))
    entries=[]
    for m in re.finditer(r'''(?i)(?:alt|title)=["']([^"']+?)(?:\s*-\s*[^"']*Skin)?["']''',text):
        label=m.group(1).strip()
        label_norm=_norm(label)
        if not label_norm or brawler_norm not in label_norm:continue
        short_norm=label_norm
        if short_norm.startswith(brawler_norm):short_norm=short_norm[len(brawler_norm):]
        if requested not in {"casuale","random"} and requested not in {label_norm,short_norm} and requested not in short_norm:continue
        fragment=text[max(0,m.start()-1800):min(len(text),m.end()+1800)]
        urls=[u for u in _extract_image_urls(fragment,page_url) if "/skins/" in u.casefold() or "brawlers/skins" in u.casefold()]
        for url in urls:entries.append((label,url))
    if requested in {"casuale","random"}:random.shuffle(entries)
    for label,url in entries:
        if _is_skin_image(url):
            clean=re.sub(rf"(?i)^\s*{re.escape(str(brawler.get('name') or ''))}\s+","",label).strip()
            return url,clean or label
    return None,None


def _skin_reference(player,brawler,skin_name,timeout=15):
    skin_url,skin_label=_italian_skin_reference(brawler,skin_name,timeout=timeout)
    if skin_url:return skin_url,skin_label
    tag=str(player.get("tag") or "").upper().replace("#","").strip()
    if not tag or not brawler or not brawler.get("id"): return None,None
    page_url=BRAWLIFY_BRAWLER_URL.format(tag=tag,brawler_id=brawler["id"])
    r=requests.get(page_url,headers={"User-Agent":"Mozilla/5.0 (SensGPT-TitaniAbusivi/1.0)","Accept-Language":"it-IT,it;q=0.9"},timeout=timeout)
    if r.status_code!=200:return None,None
    text=r.text
    requested=_norm(skin_name)
    candidates=[]; name_candidates=[]
    for m in re.finditer(r'''(?i)(?:skinName|name|title|alt)["']?\s*[:=]\s*["']([^"']{2,80})''',text):
        label=html.unescape(m.group(1)).strip(); label_norm=_norm(label)
        if not label_norm:continue
        if requested in {"casuale","random"}:
            if _norm(brawler.get("name")) in label_norm or len(label.split())>=2:name_candidates.append((label,m.start()))
        elif requested in label_norm or label_norm in requested:name_candidates.append((label,m.start()))
    if not name_candidates and requested not in {"casuale","random"}:
        for m in re.finditer(re.escape(str(skin_name)),text,re.I):name_candidates.append((str(skin_name),m.start()))
    if requested in {"casuale","random"} and name_candidates:random.shuffle(name_candidates)
    for label,pos in name_candidates:
        fragment=text[max(0,pos-2200):min(len(text),pos+2200)]
        for url in _extract_image_urls(fragment,page_url):
            lower=url.casefold()
            if any(part in lower for part in ("profile-icons","star-powers","gadgets","gears","maps/","ranked/","club-badges")):continue
            score=0
            if requested not in {"casuale","random"} and requested in _norm(url):score+=4
            if _norm(label) in _norm(url):score+=3
            if "skin" in lower:score+=2
            if "brawler" in lower or "cdn" in lower:score+=1
            candidates.append((score,label,url))
    for _,label,url in sorted(candidates,key=lambda x:x[0],reverse=True):
        if _is_skin_image(url):return url,label
    return None,None


def resolve_ai_brawler_reference(player,requested_brawler=None,timeout=15):
    try:
        brawlers,icons=_catalog(timeout=timeout)
        if requested_brawler:
            requested=str(requested_brawler).strip()
            env_match=re.match(r"^(.+?)\s+ambientazione\s+(.+)$",requested,re.I)
            if env_match:
                requested=env_match.group(1).strip(); player["ai_custom_environment"]=env_match.group(2).strip()[:120]
            skin_match=re.match(r"^(.+?)\s+skin\s+(.+)$",requested,re.I); skin_name=None
            if skin_match:
                requested=skin_match.group(1).strip(); skin_name=skin_match.group(2).strip()
            brawler=_brawler_by_name(requested,brawlers)
            if not brawler:return False,f"Brawler '{requested}' non trovato. Usa il nome italiano o quello ufficiale del Brawler."
            if skin_name:
                skin_url,resolved_name=_skin_reference(player,brawler,skin_name,timeout=timeout)
                if not skin_url:return False,f"Skin '{skin_name}' di {requested} non trovata con una reference verificabile. Nessuna generazione è stata consumata."
                player["profile_brawler"]=requested; player["profile_brawler_id"]=brawler.get("id"); player["profile_brawler_image_url"]=skin_url; player["profile_skin"]=resolved_name or skin_name; return True,None
            ok=_apply_brawler(player,brawler)
            if ok: player["profile_brawler"]=requested
            return ok,None
        icon_id=player.get("icon_id")
        if icon_id is None:
            icon_id=_icon_id_from_brawlzone(player.get("tag"),timeout=timeout)
            if icon_id is not None:player["icon_id"]=icon_id
        icon=icons.get(str(icon_id)) if icon_id is not None else None
        if icon:
            player["profile_icon_url"]=icon.get("imageUrl2") or icon.get("imageUrl"); brawler=_brawler_by_id(icon.get("brawler"),brawlers)
            if _apply_brawler(player,brawler):return True,None
        return False,"Non riesco a collegare la tua icona profilo a un Brawler. Usa: profilo ai cinematic #TAG con NOME_BRAWLER"
    except Exception as exc:
        return False,f"Reference Brawler non disponibile ({type(exc).__name__}). Riprova tra poco."
