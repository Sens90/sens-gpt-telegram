import requests

BASE="https://api.brawlapi.com"
UA={"User-Agent":"SensGPT-TitaniAbusivi/1.0"}

def _get(path):
    r=requests.get(BASE+path,headers=UA,timeout=30); r.raise_for_status(); return r.json()

def inspect_skin_master():
    skins=_get("/game/csv_logic/skins")
    chars=_get("/game/csv_logic/characters")
    it=_get("/game/localization/it")
    sample=next(iter(skins.values())) if isinstance(skins,dict) and skins else {}
    # Real playable/cosmetic rows: exclude defaults/internal/disabled rows conservatively.
    rows=list(skins.values()) if isinstance(skins,dict) else []
    cosmetics=[x for x in rows if not x.get("Disabled") and (x.get("TID") or x.get("ShopTID"))]
    tids=[x.get("TID") for x in cosmetics if x.get("TID")]
    return {"skins":len(rows),"cosmetic_candidates":len(cosmetics),"characters":len(chars) if isinstance(chars,dict) else 0,
            "it_rows":len(it) if isinstance(it,dict) else 0,"tid_candidates":len(tids),
            "skin_fields":list(sample.keys())[:80],"sample_cosmetics":cosmetics[:3]}

if __name__=="__main__": print(inspect_skin_master())
