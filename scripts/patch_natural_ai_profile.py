from pathlib import Path

p=Path('app.py')
s=p.read_text(encoding='utf-8')
old='''        r"(?:profilo ai|profilo grafico|immagine profilo|profile image)(?:\\s+(sorprendimi|brawl|cinematic|pixar|epico|fantascienza|fantasy))?\\s*#?([0289PYLQGRJCUV]{3,15})(?:\\s+con\\s+(.+?))?(?:\\s+ambientazione\\s+(.+))?",'''
new='''        r"(?:profilo ai|profilo grafico|immagine profilo|profile image)(?:\\s+(sorprendimi|brawl|cinematic|pixar|epico|fantascienza|fantasy))?\\s*#?([0289PYLQGRJCUV]{3,15})(?:\\s+(?:con\\s+)?(.+?))?(?:\\s+ambientazione\\s+(.+))?",'''
if old not in s:
    raise SystemExit('AI profile regex anchor not found')
s=s.replace(old,new,1)
old_map='''    if is_map_query:\n        search_query = (\n            f"Brawl Stars {query} {today} {context_hint} "\n            f"site:brawltrack.app/maps OR site:brawltrack.app/pro/maps "\n            f"BrawlTrack map preview Priority Picks win rate use rate Common Final Comps "\n            f"Ladder Scalata Ranked"\n        )'''
new_map='''    brawler_map_performance_query = is_map_query and any(term in query_lower for term in [\n        "win rate", "percentuale di vittoria", "percentuali di vittoria",\n        "mappe migliori", "migliori mappe", "mappa migliore", "che mappa",\n        "quale mappa", "mappe con", "per ogni modalità", "per ogni modalita"\n    ])\n\n    if brawler_map_performance_query:\n        search_query = (\n            f"Brawl Stars {query} {today} {context_hint} "\n            f"site:brawltrack.app/brawlers "\n            f"BrawlTrack Best Maps Best Game Modes win rate battles"\n        )\n    elif is_map_query:\n        search_query = (\n            f"Brawl Stars {query} {today} {context_hint} "\n            f"site:brawltrack.app/maps OR site:brawltrack.app/pro/maps "\n            f"BrawlTrack map preview Priority Picks win rate use rate Common Final Comps "\n            f"Ladder Scalata Ranked"\n        )'''
if old_map in s:
    s=s.replace(old_map,new_map,1)
p.write_text(s,encoding='utf-8')
print('Natural AI profile syntax and Brawler map lookup patched')
