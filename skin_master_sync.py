import requests, collections
BASE="https://api.brawlapi.com"; UA={"User-Agent":"SensGPT-TitaniAbusivi/1.0"}
def _get(path):
 r=requests.get(BASE+path,headers=UA,timeout=30); r.raise_for_status(); return r.json()
def inspect_skin_master():
 skins=_get("/game/csv_logic/skins"); chars=_get("/game/csv_logic/characters"); it=_get("/game/localization/it")
 rows=list(skins.values()) if isinstance(skins,dict) else []; cosmetics=[x for x in rows if not x.get("Disabled") and x.get("TID")]
 # Character rows contain the configured default/skin linkage; inspect only compact linkage fields.
 cr=list(chars.values()) if isinstance(chars,dict) else []
 samplec=cr[0] if cr else {}; link_fields=[k for k in samplec if any(w in k.lower() for w in ("skin","name","tid","id"))]
 char_links=[{k:x.get(k) for k in link_fields[:25]} for x in cr[:8]]
 itkeys=set(it.keys()) if isinstance(it,dict) else set()
 return {"raw":len(rows),"cosmetics":len(cosmetics),"available":sum(x.get("OdditiesShopAvailability")=="AVAILABLE" for x in cosmetics),
 "catalog_eligible":sum(not x.get("DisableCatalogRelease") and not x.get("NotSoldInVault") for x in cosmetics),
 "it_exact_tid":sum(x.get("TID") in itkeys for x in cosmetics),"character_link_fields":link_fields[:40],"character_link_samples":char_links}
if __name__=="__main__": print(inspect_skin_master())
