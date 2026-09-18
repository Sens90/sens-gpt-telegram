import os, requests, collections, json
BASE="https://api.brawlapi.com"; UA={"User-Agent":"SensGPT-TitaniAbusivi/1.0"}
def _get(p):
 r=requests.get(BASE+p,headers=UA,timeout=30); r.raise_for_status(); return r.json()
def build_verified_rows():
 skins=_get("/game/csv_logic/skins"); chars=_get("/game/csv_logic/characters"); confs=_get("/game/csv_logic/skin_confs"); it=_get("/game/localization/it"); en=_get("/game/localization/en")
 cosmetics=[x for x in skins.values() if not x.get("Disabled") and x.get("TID")]
 char_by_internal={x.get("Name"):x for x in chars.values() if x.get("id") and x.get("Name") and x.get("ItemName")}
 conf_by_name={x.get("Name"):x for x in confs.values() if x.get("Name")}
 out=[]; unmapped=[]
 for s in cosmetics:
  cf=conf_by_name.get(s.get("Conf") or s.get("Name")); raw_char=cf.get("Character") if cf else None
  char_key=str(raw_char or "").split(";")[0].strip()
  ch=char_by_internal.get(char_key)
  if not ch:
   tid=str(s.get("TID") or "")
   token=tid[4:].split("_",1)[0] if tid.startswith("TID_") else ""
   candidates=[x for x in chars.values() if x.get("id") and x.get("ItemName") and (str(x.get("ItemName")).upper()==token or str(x.get("Name")).upper()==token)]
   if len(candidates)==1: ch=candidates[0]; char_key=str(ch.get("Name") or char_key)
   elif token=="RICO":
    base=next((x for x in skins.values() if str(x.get("Name") or "")=="TrickshotDefault"),None)
    base_cf=conf_by_name.get((base or {}).get("Conf") or (base or {}).get("Name")) if base else None
    base_key=str((base_cf or {}).get("Character") or "").split(";")[0].strip()
    base_ch=char_by_internal.get(base_key)
    if base_ch: ch=base_ch; char_key=base_key
  loc=it.get(s.get("TID")) or {}; loc_en=en.get(s.get("TID")) or {}
  if not ch: unmapped.append(s.get("id")); continue
  name_it=loc.get("IT") if isinstance(loc,dict) else None
  out.append({"external_id":str(s["id"]),"brawler_id":ch["id"],"brawler_name":str(ch["ItemName"]).upper(),"name_en":(loc_en.get("EN") if isinstance(loc_en,dict) else None) or s["Name"],"name_it":name_it,"rarity":s.get("Rarity"),"price_gems":s.get("PriceGems"),"source":"brawlapi_game_csv","source_url":BASE+"/game/csv_logic/skins","source_payload":{"tid":s.get("TID"),"conf":s.get("Conf"),"character":char_key},"verification_status":"structured_verified","name_it_source":"brawlapi_game_localization_it","name_it_source_url":BASE+"/game/localization/it"})
 return out,unmapped
def inspect_skin_master():
 rows,unmapped=build_verified_rows(); return {"mapped":len(rows),"unmapped":len(unmapped),"sample":rows[:5]}
def sync_verified_rows():
 rows,unmapped=build_verified_rows(); url=os.getenv("SUPABASE_URL"); key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
 if not url or not key: return {"ok":False,"reason":"supabase env missing","mapped":len(rows)}
 h={"apikey":key,"Authorization":"Bearer "+key,"Content-Type":"application/json","Prefer":"resolution=merge-duplicates,return=minimal"}
 # Insert only new stable game IDs. Existing hand-verified rows are intentionally preserved.
 ids=set(); start=0; page=1000
 while True:
  ph=dict(h); ph["Range"]=f"{start}-{start+page-1}"
  existing=requests.get(url+"/rest/v1/skins_catalog?select=external_id&order=id.asc",headers=ph,timeout=30); existing.raise_for_status(); batch=existing.json()
  ids.update(x["external_id"] for x in batch)
  if len(batch)<page: break
  start += page
 new=[x for x in rows if x["external_id"] not in ids]
 # Upsert all structured rows so authoritative EN/IT localization stays current.
 for i in range(0,len(rows),800):
  r=requests.post(url+"/rest/v1/skins_catalog?on_conflict=source,external_id",headers=h,json=rows[i:i+800],timeout=60); r.raise_for_status()
 return {"ok":True,"mapped":len(rows),"unmapped":len(unmapped),"existing_preserved":len(rows)-len(new),"inserted":len(new),"localized_upserted":len(rows)}

def inspect_unmapped_relations():
 skins=_get("/game/csv_logic/skins"); chars=_get("/game/csv_logic/characters"); confs=_get("/game/csv_logic/skin_confs")
 char_by_internal={x.get("Name"):x for x in chars.values() if x.get("id") and x.get("Name") and x.get("ItemName")}
 conf_by_name={x.get("Name"):x for x in confs.values() if x.get("Name")}
 fields=set()
 out=[]
 for s in skins.values():
  if s.get("Disabled") or not s.get("TID"): continue
  cf=conf_by_name.get(s.get("Conf") or s.get("Name"))
  if cf and char_by_internal.get(str(cf.get("Character") or "").split(";")[0].strip()): continue
  if cf: fields.update(cf.keys())
  out.append({"skin_id":s.get("id"),"skin":s.get("Name"),"conf":s.get("Conf"),"skin_fields":{k:v for k,v in s.items() if v not in (None,"",0,False,[])}, "conf_fields":{k:v for k,v in (cf or {}).items() if v not in (None,"",0,False,[])}})
 return {"count":len(out),"candidate_fields":sorted(fields),"rows":[{"skin_id":r["skin_id"],"skin":r["skin"],"conf":r["conf"],"character":r["conf_fields"].get("Character"),"progression_base":r["skin_fields"].get("ProgressionSkinBase"),"tid":r["skin_fields"].get("TID")} for r in out]}

if __name__=="__main__": print(inspect_unmapped_relations())
