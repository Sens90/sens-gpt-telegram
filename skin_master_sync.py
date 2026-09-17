import requests, collections
BASE="https://api.brawlapi.com"; UA={"User-Agent":"SensGPT-TitaniAbusivi/1.0"}
def _get(p):
 r=requests.get(BASE+p,headers=UA,timeout=30); r.raise_for_status(); return r.json()
def inspect_skin_master():
 skins=_get("/game/csv_logic/skins"); chars=_get("/game/csv_logic/characters"); confs=_get("/game/csv_logic/skin_confs"); it=_get("/game/localization/it")
 cosmetics=[x for x in skins.values() if not x.get("Disabled") and x.get("TID")]
 char_by_internal={x.get("Name"):x for x in chars.values() if x.get("id") and x.get("Name") and x.get("ItemName")}
 conf_by_name={x.get("Name"):x for x in confs.values() if x.get("Name")}
 mapped=[]; unmapped=[]
 for s in cosmetics:
  cf=conf_by_name.get(s.get("Conf") or s.get("Name")); ch=char_by_internal.get(cf.get("Character")) if cf else None
  if ch: mapped.append((s,ch,cf))
  else: unmapped.append({"skin_id":s.get("id"),"name":s.get("Name"),"conf":s.get("Conf")})
 by_b=collections.Counter(ch["id"] for _,ch,_ in mapped)
 loc=[]
 for s,ch,cf in mapped[:12]: loc.append({"skin_id":s["id"],"skin":s["Name"],"tid":s["TID"],"it":it.get(s["TID"]),"brawler_id":ch["id"],"brawler":ch["ItemName"],"character":cf["Character"]})
 return {"cosmetics":len(cosmetics),"mapped":len(mapped),"unmapped":len(unmapped),"mapped_brawlers":len(by_b),"duplicate_skin_ids":len(mapped)-len({s["id"] for s,_,_ in mapped}),"unmapped_sample":unmapped[:20],"localized_mapping_sample":loc}
if __name__=="__main__": print(inspect_skin_master())
