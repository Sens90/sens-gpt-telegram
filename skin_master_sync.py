import requests
BASE="https://api.brawlapi.com"; UA={"User-Agent":"SensGPT-TitaniAbusivi/1.0"}
def _get(path):
 r=requests.get(BASE+path,headers=UA,timeout=30); r.raise_for_status(); return r.json()
def inspect_skin_master():
 skins=_get("/game/csv_logic/skins"); chars=_get("/game/csv_logic/characters"); confs=_get("/game/csv_logic/skin_confs"); it=_get("/game/localization/it")
 cosmetics=[x for x in skins.values() if not x.get("Disabled") and x.get("TID")]
 playable=[x for x in chars.values() if not x.get("Disabled") and x.get("ItemName") and x.get("DefaultSkin")]
 defaults={x["DefaultSkin"]:(x["id"],x["ItemName"],x["Name"]) for x in playable}
 cr=list(confs.values()) if isinstance(confs,dict) else []
 sample=cr[0] if cr else {}; fields=[k for k in sample if any(w in k.lower() for w in ("skin","character","name","id"))]
 samples=[{k:x.get(k) for k in fields[:30]} for x in cr[:8]]
 return {"cosmetics":len(cosmetics),"playable_characters":len(playable),"default_links":len(defaults),"skin_confs":len(cr),"skin_conf_link_fields":fields[:50],"skin_conf_samples":samples}
if __name__=="__main__": print(inspect_skin_master())
