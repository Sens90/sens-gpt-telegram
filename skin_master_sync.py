import requests
BASE="https://api.brawlapi.com"; UA={"User-Agent":"SensGPT-TitaniAbusivi/1.0"}
def _get(path):
 r=requests.get(BASE+path,headers=UA,timeout=30); r.raise_for_status(); return r.json()
def inspect_skin_master():
 skins=_get("/game/csv_logic/skins"); chars=_get("/game/csv_logic/characters"); it=_get("/game/localization/it")
 rows=list(skins.values()) if isinstance(skins,dict) else []
 cosmetics=[x for x in rows if not x.get("Disabled") and x.get("TID")]
 available=[x for x in cosmetics if x.get("OdditiesShopAvailability")=="AVAILABLE"]
 catalog=[x for x in cosmetics if not x.get("DisableCatalogRelease") and not x.get("NotSoldInVault")]
 itkeys=set(it.keys()) if isinstance(it,dict) else set()
 localized=sum(1 for x in cosmetics if x.get("TID") in itkeys)
 return {"raw":len(rows),"cosmetics":len(cosmetics),"available":len(available),"catalog_eligible":len(catalog),"it_exact_tid":localized,"it_rows":len(itkeys),"sample":cosmetics[:2]}
if __name__=="__main__": print(inspect_skin_master())
