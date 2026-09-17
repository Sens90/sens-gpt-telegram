import os, requests, collections, json
BASE="https://api.brawlapi.com"; UA={"User-Agent":"SensGPT-TitaniAbusivi/1.0"}
def _get(p):
 r=requests.get(BASE+p,headers=UA,timeout=30); r.raise_for_status(); return r.json()
def build_verified_rows():
 skins=_get("/game/csv_logic/skins"); chars=_get("/game/csv_logic/characters"); confs=_get("/game/csv_logic/skin_confs"); it=_get("/game/localization/it")
 cosmetics=[x for x in skins.values() if not x.get("Disabled") and x.get("TID")]
 char_by_internal={x.get("Name"):x for x in chars.values() if x.get("id") and x.get("Name") and x.get("ItemName")}
 conf_by_name={x.get("Name"):x for x in confs.values() if x.get("Name")}
 out=[]; unmapped=[]
 for s in cosmetics:
  cf=conf_by_name.get(s.get("Conf") or s.get("Name")); ch=char_by_internal.get(cf.get("Character")) if cf else None; loc=it.get(s.get("TID")) or {}
  if not ch: unmapped.append(s.get("id")); continue
  name_it=loc.get("IT") if isinstance(loc,dict) else None
  out.append({"external_id":str(s["id"]),"brawler_id":ch["id"],"brawler_name":str(ch["ItemName"]).upper(),"name_en":s["Name"],"name_it":name_it,"rarity":s.get("Rarity"),"price_gems":s.get("PriceGems"),"source":"brawlapi_game_csv","source_url":BASE+"/game/csv_logic/skins","source_payload":{"tid":s.get("TID"),"conf":s.get("Conf"),"character":cf.get("Character")},"verification_status":"structured_verified","image_verified":False,"name_it_source":"brawlapi_game_localization_it","name_it_source_url":BASE+"/game/localization/it"})
 return out,unmapped
def inspect_skin_master():
 rows,unmapped=build_verified_rows(); return {"mapped":len(rows),"unmapped":len(unmapped),"sample":rows[:5]}
def sync_verified_rows():
 rows,unmapped=build_verified_rows(); url=os.getenv("SUPABASE_URL"); key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
 if not url or not key: return {"ok":False,"reason":"supabase env missing","mapped":len(rows)}
 h={"apikey":key,"Authorization":"Bearer "+key,"Content-Type":"application/json","Prefer":"resolution=merge-duplicates,return=minimal"}
 # Insert only new stable game IDs. Existing hand-verified rows are intentionally preserved.
 existing=requests.get(url+"/rest/v1/skins_catalog?select=external_id",headers=h,timeout=30); existing.raise_for_status(); ids={x["external_id"] for x in existing.json()}
 new=[x for x in rows if x["external_id"] not in ids]
 for i in range(0,len(new),800):
  r=requests.post(url+"/rest/v1/skins_catalog",headers=h,json=new[i:i+800],timeout=60); r.raise_for_status()
 return {"ok":True,"mapped":len(rows),"unmapped":len(unmapped),"existing_preserved":len(rows)-len(new),"inserted":len(new)}

def inspect_unmapped_relations():
 skins=_get("/game/csv_logic/skins"); chars=_get("/game/csv_logic/characters"); confs=_get("/game/csv_logic/skin_confs")
 char_by_internal={x.get("Name"):x for x in chars.values() if x.get("id") and x.get("Name")}
 conf_by_name={x.get("Name"):x for x in confs.values() if x.get("Name")}
 fields=set()
 out=[]
 for s in skins.values():
  if s.get("Disabled") or not s.get("TID"): continue
  cf=conf_by_name.get(s.get("Conf") or s.get("Name"))
  if cf and char_by_internal.get(cf.get("Character")): continue
  if cf: fields.update(cf.keys())
  out.append({"skin_id":s.get("id"),"skin":s.get("Name"),"conf":s.get("Conf"),"skin_fields":{k:v for k,v in s.items() if v not in (None,"",0,False,[])}, "conf_fields":{k:v for k,v in (cf or {}).items() if v not in (None,"",0,False,[])}})
 return {"count":len(out),"candidate_fields":sorted(fields),"rows":out}

if __name__=="__main__": print(inspect_unmapped_relations())
