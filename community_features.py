import os
import re
import io
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ROME = ZoneInfo("Europe/Rome")

FAQ_TEXT = (
    "TITANI ABUSIVI - INFO RAPIDE\n\n"
    "- Club competitivo: minimo 100.000 trofei.\n"
    "- Ranked minimo: Leggenda.\n"
    "- I club della community, in ordine, sono: TITANI ABUSIVI, TAMARRI ABUSIVI, TORNADI ABUSIVI, TALENTI ABUSIVI.\n"
    "- In base a requisiti, attività e disponibilità posti puoi essere spostato tra i quattro club.\n"
    "- Gli eventi dichiarati obbligatori, come il Megasalvadanaio, vanno completati: chi non partecipa può essere espulso indipendentemente da coppe o ruolo.\n"
    "- Telegram e Discord sono obbligatori quando richiesti per tornei/eventi.\n"
    "- Se sei assente per studio, lavoro o vacanze avvisa la direzione o usa il comando assenza.\n"
    "- Reclutamento: titaniabusivi.it"
)

HELP_TEXT = """COMANDI SENS GPT - SOCI

ACCOUNT E PROFILO
- registrami #TAG — collega il tuo account Brawl Stars
- profilo #TAG / stats #TAG — scheda completa del giocatore
- ranked #TAG — Classificata attuale e record
- storico ranked #TAG — ultime variazioni della Classificata

CLASSIFICHE COMMUNITY
- classifica — mostra i periodi disponibili
- classifica oggi / 7 / 15 / 30 — andamento trofei
- classifica trofei
- classifica brawler
- classifica livello
- classifica prestigio
- classifica 3v3
- classifica solo
- classifica duo
- classifica classificata / classifica ranked — Classificata attuale\n- classifica ranked oggi / 7 / 15 / 30 — variazione ELO nel periodo
- classifica classificata stagione / classifica ranked stagione — record stagione
- classifica classificata carriera / classifica ranked carriera — record carriera
- statistiche / tutte le classifiche — riepilogo statistiche

CLASSIFICHE DEI 4 CLUB
Aggiungi titani, tamarri, tornadi o talenti alla classifica.
Esempi: classifica titani 3v3; classifica tamarri trofei; classifica tornadi prestigio; classifica talenti duo.
- statistiche titani / tamarri / tornadi / talenti — riepilogo del club

GRAFICI
- grafico 7|15|30|90 #TAG — andamento di un giocatore
- grafico community 7|15|30|90 — totale giocatori registrati
- grafico titani 7|15|30|90
- grafico tamarri 7|15|30|90
- grafico tornadi 7|15|30|90
- grafico talenti 7|15|30|90

COMMUNITY
- club — riepilogo community
- elenco registrati — account collegati
- inattivi — situazione inattività visibile dal bot
- assenza N — segnala N giorni di assenza
- eventi — eventi aperti
- partecipo ID — conferma partecipazione
- regole / faq — regolamento
- sito — sito ufficiale
- discord — server Discord

BRAWL STARS
Puoi inoltre chiedere direttamente a Sens GPT meta, mappe, composizioni, Ladder o Classificata, Brawler, configurazioni, gadget, abilità stellari, equipaggiamenti, overdrive e consigli su cosa pushare.

Scrivi comandi in qualsiasi momento per rivedere questa guida."""


class CommunityFeatures:
    def __init__(
        self,
        supabase_url,
        supabase_key,
        player_fetcher,
        history_fetcher,
        change_calculator,
        number_formatter,
        change_formatter,
        snapshot_saver=None,
    ):
        self.supabase_url = (supabase_url or "").rstrip("/")
        self.supabase_key = supabase_key or ""
        self.player_fetcher = player_fetcher
        self.history_fetcher = history_fetcher
        self.change_calculator = change_calculator
        self.number_formatter = number_formatter
        self.change_formatter = change_formatter
        self.snapshot_saver = snapshot_saver

    @property
    def ready(self):
        return bool(self.supabase_url and self.supabase_key)

    def _headers(self, prefer=None):
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _get(self, table, params=None):
        if not self.ready:
            return []
        response = requests.get(
            f"{self.supabase_url}/rest/v1/{table}",
            headers=self._headers(),
            params=params or {},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _post(self, table, payload, params=None, prefer="return=representation"):
        if not self.ready:
            return []
        response = requests.post(
            f"{self.supabase_url}/rest/v1/{table}",
            headers=self._headers(prefer),
            params=params or {},
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        if not response.text:
            return []
        return response.json()

    def _patch(self, table, payload, params=None, prefer="return=minimal"):
        if not self.ready:
            return []
        response = requests.patch(
            f"{self.supabase_url}/rest/v1/{table}",
            headers=self._headers(prefer),
            params=params or {},
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        if not response.text:
            return []
        return response.json()

    def _now_iso(self):
        return datetime.now(timezone.utc).isoformat()

    def _draft_identity(self, raw_name, raw_mode=None):
        """Resolve Italian/English map and mode names to one canonical identity."""
        wanted=str(raw_name or "").strip().casefold()
        wanted_mode=str(raw_mode or "").strip().casefold()
        if not wanted:return None
        # Lazy import avoids the existing live_maps -> community_features startup hook cycle.
        from live_maps import safe_get, localized, brawltrack_pro_map_stats
        names=safe_get("i18n/names.it.json.gz") or {}
        rotation=safe_get("event_rotation.json.gz") or []
        # The analyzer Ranked dataset is the complete map catalog; rotation only
        # contains maps active now and must never limit Draft map recognition.
        ranked_catalog=safe_get("pl-results.json.gz") or {}
        mode_aliases={
            "gem grab":"Gem Grab","arraffagemme":"Gem Grab",
            "brawl ball":"Brawl Ball","footbrawl":"Brawl Ball",
            "hot zone":"Hot Zone","zona rovente":"Hot Zone",
            "bounty":"Bounty","ricercati":"Bounty",
            "heist":"Heist","rapina":"Heist",
            "knockout":"Knockout","k.o.":"Knockout","ko":"Knockout",
            "wipeout":"Wipeout","annientamento":"Wipeout",
        }
        # First retain exact mode information from the current rotation when present.
        candidates=[]
        for event in rotation if isinstance(rotation,list) else []:
            en=str(event.get("event_map") or event.get("map") or "").strip()
            if en:candidates.append((en,str(event.get("event_mode") or event.get("mode") or ""),event.get("event_map_id")))
        # Then add every map in the complete Ranked dataset. Its key is the canonical
        # analyzer map id; resolve the English name from i18n instead of guessing.
        map_names=names.get("maps",{}) if isinstance(names,dict) else {}
        it_to_en={str(v).casefold():str(k) for k,v in map_names.items() if v}
        for key in ranked_catalog.keys() if isinstance(ranked_catalog,dict) else []:
            key_text=str(key)
            # Analyzer ids normally match normalized English names. Prefer exact
            # i18n reverse matches and otherwise compare normalized localized keys.
            en=None
            for candidate_en in map_names.keys():
                norm=re.sub(r"[^a-z0-9]+","_",str(candidate_en).casefold()).strip("_")
                if norm==key_text.casefold():
                    en=str(candidate_en);break
            if en and not any(x[0].casefold()==en.casefold() for x in candidates):
                candidates.append((en,"",key_text))
        for en,raw_event_mode,canonical_key in candidates:
            it=localized(names,"maps",en)
            if wanted not in {en.casefold(),str(it).casefold()}:continue
            en_mode=mode_aliases.get(raw_event_mode.casefold(), raw_event_mode)
            it_mode=localized(names,"modes",en_mode) if en_mode else ""
            accepted={raw_event_mode.casefold(),str(en_mode).casefold(),str(it_mode).casefold()}-{""}
            accepted.update(k for k,v in mode_aliases.items() if en_mode and v.casefold()==str(en_mode).casefold())
            if wanted_mode and accepted and wanted_mode not in accepted:return None
            stats=brawltrack_pro_map_stats(en) or {}
            return {"map_en":en,"map_it":it,"map_id":stats.get("map_id"),"mode_en":en_mode or None,"mode_it":it_mode or None,"catalog_key":canonical_key}
        return None

    @staticmethod
    def _ranked_draft_format(rank_name):
        """Current Ranked 2.0 format: all-pick through Gold, ban+all-pick Diamond, turn-pick Mythic+."""
        value=str(rank_name or "").strip().casefold()
        if not value:
            return "unknown"
        if any(x in value for x in ("bronzo","bronze","argento","silver","oro","gold")):
            return "all_pick"
        if any(x in value for x in ("diamante","diamond")):
            return "ban_all_pick"
        if any(x in value for x in ("mito","mitic","mythic","leggenda","legend","maestro","master","campion","pro")):
            return "turn_pick"
        return "unknown"

    @staticmethod
    def _draft_pick_order(first_pick):
        # Official Mythic+ snake: 1-2-2-1. Values are relative to the user's team.
        return ["my","enemy","enemy","my","my","enemy"] if first_pick == "my" else ["enemy","my","my","enemy","enemy","my"]

    def _draft_next_turn_text(self, state):
        if state.get("draft_format") != "turn_pick":
            return None
        first=state.get("first_pick")
        if first not in ("my","enemy"):
            return "Indica chi ha il primo pick: primo pick nostro oppure primo pick avversario."
        index=len(state.get("pick_sequence") or [])
        if index >= 6:
            return "Draft pick completata: 3 nostri e 3 avversari."
        side=self._draft_pick_order(first)[index]
        return f"Pick {index+1}/6: {'NOSTRA SQUADRA' if side == 'my' else 'AVVERSARIO'}."

    def draft_map_advice_text(self, map_name, rank_name=None, mode=None):
        """Fast Ranked draft opener using canonical IT/EN identity and verified competitive map data."""
        identity=self._draft_identity(map_name,mode)
        if not identity:return None
        from live_maps import brawltrack_pro_map_stats
        stats=brawltrack_pro_map_stats(identity["map_en"]) or {}
        picks=stats.get("priority_picks") or stats.get("picks") or []
        avoid=stats.get("avoid") or stats.get("avoid_these") or []
        catalog=self._get("brawlers_catalog",{"select":"name,name_it"})
        it_by_en={str(x.get("name") or "").casefold():str(x.get("name_it") or x.get("name") or "") for x in catalog or []}
        def local_brawler(value):
            return it_by_en.get(str(value or "").casefold(),str(value or "").title())
        pick_names=[]
        for item in picks if isinstance(picks,list) else []:
            name=item.get("brawler") if isinstance(item,dict) else item
            if name and local_brawler(name) not in pick_names:pick_names.append(local_brawler(name))
            if len(pick_names)>=5:break
        avoid_names=[]
        for item in avoid if isinstance(avoid,list) else []:
            name=item.get("brawler") if isinstance(item,dict) else item
            if name and local_brawler(name) not in avoid_names:avoid_names.append(local_brawler(name))
            if len(avoid_names)>=5:break
        title=f'RANKED - {identity["map_it"].upper()}'
        lines=[title]
        if identity.get("mode_it"):
            lines.append(f'Modalità: {identity["mode_it"]}')
        if rank_name:lines.append(f'Fascia Ranked: {rank_name}')
        if avoid_names:lines.append("Ban/evita: "+", ".join(avoid_names))
        if pick_names:lines.append("Migliori pick: "+", ".join(pick_names))
        if not avoid_names and not pick_names:
            lines.append("La mappa è riconosciuta, ma non ho ancora pick/ban verificati da mostrare.")
        return "\n".join(lines)

    def draft_comp_advice_text(self, draft_state):
        """Recommend only teammates evidenced by BrawlTrack common final comps."""
        if not draft_state or not draft_state.get("map"):
            return None
        from live_maps import brawltrack_pro_map_stats
        stats=brawltrack_pro_map_stats(draft_state["map"]) or {}
        comps=stats.get("final_comps") or []
        selected=[str(x).strip().casefold() for x in (draft_state.get("my_picks") or []) if x]
        if not selected or not comps:
            return None
        catalog=self._get("brawlers_catalog",{"select":"name,name_it"})
        aliases={}
        labels={}
        for row in catalog or []:
            en=str(row.get("name") or "").strip()
            it=str(row.get("name_it") or en).strip()
            if en:
                aliases[en.casefold()]=en.casefold()
                labels[en.casefold()]=it
            if it:
                aliases[it.casefold()]=en.casefold()
        chosen={aliases.get(x,x) for x in selected}
        matches=[]
        for comp in comps if isinstance(comps,list) else []:
            team=[str(x).strip().casefold() for x in (comp.get("team") or []) if x] if isinstance(comp,dict) else []
            if chosen and chosen.issubset(set(team)):
                remaining=[labels.get(x,str(x).title()) for x in team if x not in chosen]
                if remaining:
                    matches.append((float(comp.get("win_rate") or 0),int(comp.get("sets") or 0),remaining))
        if not matches:
            return None
        matches.sort(key=lambda x:(x[1],x[0]),reverse=True)
        wr,sets,remaining=matches[0]
        return "Comp BrawlTrack compatibile: "+", ".join(remaining)+f" — {sets} set, {wr:g}% WR"

    def brawler_counter_text(self, brawler_name, mode=None, map_name=None):
        """Return verified counter data stored server-side; never invent matchups."""
        try:
            catalog = self._get("brawlers_catalog", {"select": "id,name,name_it"})
            wanted = str(brawler_name or "").strip().casefold()
            target = next((b for b in catalog if wanted in {
                str(b.get("name") or "").casefold(), str(b.get("name_it") or "").casefold()
            }), None)
            if not target:
                return None
            params = {
                "select": "counter_brawler_id,mode,map_name,score,sample_size,source,source_updated_at",
                "brawler_id": f"eq.{target['id']}",
                "order": "score.desc.nullslast",
                "limit": "10",
            }
            if mode:
                params["mode"] = f"eq.{mode}"
            if map_name:
                params["map_name"] = f"eq.{map_name}"
            rows = self._get("brawler_counters", params)
            if not rows:
                return (
                    f"Non ho ancora counter verificati per {target.get('name_it') or target.get('name')}. "
                    "Non invento matchup: il dato verrà mostrato quando sarà disponibile nella cache counter."
                )
            names = {int(b["id"]): (b.get("name_it") or b.get("name")) for b in catalog if b.get("id") is not None}
            title = f"COUNTER DI {(target.get('name_it') or target.get('name')).upper()}"
            if map_name: title += f" - {map_name}"
            elif mode: title += f" - {mode}"
            lines = [title, ""]
            for i,row in enumerate(rows[:5],1):
                label = names.get(int(row["counter_brawler_id"]), str(row["counter_brawler_id"]))
                detail = f" - indice {float(row['score']):.1f}" if row.get("score") is not None else ""
                lines.append(f"{i}. {label}{detail}")
            return "\n".join(lines)
        except Exception as exc:
            print("ERRORE COUNTER BRAWLER:", repr(exc), flush=True)
            return None

    def get_registered_user(self, telegram_user_id):
        """Resolve an active registered member immediately from Telegram identity."""
        if not self.ready or telegram_user_id is None:
            return None
        try:
            rows=self._get("community_members",{
                "select":"*",
                "telegram_user_id":f"eq.{int(telegram_user_id)}",
                "is_active":"eq.true",
                "player_tag":"not.is.null",
                "order":"player_last_updated_at.desc.nullslast",
                "limit":"1",
            })
            return rows[0] if rows else None
        except Exception as exc:
            print("ERRORE RICONOSCIMENTO UTENTE:",repr(exc),flush=True)
            return None

    @staticmethod
    def _skin_key(value):
        return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

    def _official_owned_skin_ids(self, player_tag):
        """Read owned skin IDs from the verified official skins endpoint via our private proxy."""
        tag=str(player_tag or "").strip().lstrip("#").upper()
        proxy_url=(os.environ.get("BRAWL_OFFICIAL_PROXY_URL") or "").strip()
        proxy_key=(os.environ.get("BRAWL_OFFICIAL_PROXY_KEY") or "").strip()
        if not tag or not proxy_url or not proxy_key:
            raise RuntimeError("Official skins proxy is not configured")
        response=requests.get(proxy_url,params={"action":"skins","tag":tag},headers={"X-Sens-Key":proxy_key,"Accept":"application/json","User-Agent":"SensGPT/1.0"},timeout=20)
        response.raise_for_status()
        payload=response.json()
        items=payload.get("items") if isinstance(payload,dict) else None
        if not isinstance(items,list):
            raise RuntimeError("Official skins payload missing items")
        owned=set()
        for brawler in items:
            if not isinstance(brawler,dict): continue
            for skin in brawler.get("skins") or []:
                if isinstance(skin,dict) and skin.get("id") is not None:
                    try: owned.add(int(skin["id"]))
                    except (TypeError,ValueError): pass
        return owned

    @staticmethod
    def _skin_category_label(row):
        """Italian display category without inventing a rarity missing from game data."""
        rarity = str(row.get("rarity") or "").upper()
        labels = {
            "RARE": "Rare", "SUPER_RARE": "Super rare", "EPIC": "Epiche",
            "MYTHIC": "Mitiche", "LEGENDARY": "Leggendarie",
            "HYPERCHARGE": "Skin Overdrive", "COLLECTORS": "Collezione",
            "RANKED_PASS": "Pass Pro",
        }
        if rarity:
            return labels.get(rarity, rarity.replace("_", " ").title())
        tid = str((row.get("source_payload") or {}).get("tid") or "").upper()
        conf = str((row.get("source_payload") or {}).get("conf") or "").upper()
        if "TRUE_GOLD" in tid or conf.endswith("GOLD") or conf.endswith("_GOLD"):
            return "Oro 24 carati"
        if "TRUE_SILVER" in tid or conf.endswith("SILVER") or conf.endswith("_SILVER"):
            return "Argento"
        if "PROPASS_PROGRESSION" in tid:
            return "Brawl Pass"
        return "Senza rarità"

    def _resolve_skin_brawler_name(self, value):
        wanted = self._skin_key(value)
        try:
            catalog = self._get("brawlers_catalog", {"select": "name,name_it"})
            for row in catalog or []:
                if wanted in (self._skin_key(row.get("name")), self._skin_key(row.get("name_it"))):
                    return str(row.get("name") or row.get("name_it") or value)
        except Exception as exc:
            print("ERRORE RISOLUZIONE BRAWLER SKIN:", repr(exc), flush=True)
        return str(value or "").strip()

    def _brawlvalue_skin_image(self, brawler_name, skin_name):
        try:
            from bs4 import BeautifulSoup
            slug = re.sub(r"[^a-z0-9]+", "-", str(brawler_name or "").casefold()).strip("-")
            if not slug:
                return None
            response = requests.get(f"https://brawlvalue.com/en/skins/{slug}", timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            wanted = self._skin_key(skin_name)
            for img in soup.find_all("img"):
                alt = str(img.get("alt") or "")
                alt_key = self._skin_key(re.sub(r"\\s*-\\s*[^-]*skin\\s*$", "", alt, flags=re.I))
                if alt_key != wanted:
                    continue
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://brawlvalue.com" + src
                if src.startswith(("https://", "http://")):
                    return src
        except Exception as exc:
            print("ERRORE IMMAGINE BRAWL VALUE:", repr(exc), flush=True)
        return None

    async def send_skin_image(self, message, brawler_name, skin_name):
        try:
            canonical_brawler = self._resolve_skin_brawler_name(brawler_name)
            rows = self._get("skins_catalog", {
                "select": "external_id,name_en,name_it,brawler_name",
                "verification_status": "eq.structured_verified",
                "brawler_name": f"eq.{canonical_brawler}",
                "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
            })
            wanted = self._skin_key(skin_name)
            matches = [r for r in rows or [] if wanted in (self._skin_key(r.get("name_it")), self._skin_key(r.get("name_en")))]
            if len(matches) != 1:
                return "Non trovo una corrispondenza univoca per questa skin."
            row = matches[0]
            image_url = self._brawlvalue_skin_image(canonical_brawler, row.get("name_en") or row.get("name_it"))
            if not image_url:
                return "Ho trovato la skin nel catalogo, ma Brawl Value non mi ha restituito un'immagine verificabile."
            image = requests.get(image_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            image.raise_for_status()
            if not str(image.headers.get("Content-Type") or "").lower().startswith("image/"):
                return "L'immagine trovata non è valida."
            if len(image.content) > 10 * 1024 * 1024:
                return "L'immagine della skin è troppo grande per essere inviata."
            await message.reply_photo(photo=image.content, caption=str(row.get("name_it") or row.get("name_en") or skin_name))
            return True
        except Exception as exc:
            print("ERRORE INVIO SKIN IMAGE:", repr(exc), flush=True)
            return "Non riesco a recuperare l'immagine della skin in questo momento."

    def skin_account_text(self, registered_user, brawler_name=None, rarity=None, category=None, mode="summary"):
        if not registered_user or not registered_user.get("player_tag"):
            return "Devi prima registrare il tuo tag Brawl Stars."
        try:
            catalog = []
            page_size = 1000
            offset = 0
            while True:
                page = self._get("skins_catalog", {
                    "select": "external_id,name_en,name_it,rarity,brawler_name,source_payload",
                    "verification_status": "eq.structured_verified",
                    "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
                    "order": "brawler_name.asc,name_en.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                })
                catalog.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
            if not catalog:
                return "Il catalogo skin non è disponibile in questo momento."
            owned_ids = self._official_owned_skin_ids(registered_user["player_tag"])
            rows = list(catalog)
            if brawler_name:
                canonical_brawler = self._resolve_skin_brawler_name(brawler_name)
                wanted = self._skin_key(canonical_brawler)
                matches = [r for r in rows if self._skin_key(r.get("brawler_name")) == wanted]
                if not matches:
                    return f"Non trovo il Brawler {brawler_name} nel catalogo skin."
                rows = matches
            if rarity:
                wanted_rarity = self._skin_key(rarity)
                rows = [r for r in rows if self._skin_key(r.get("rarity")) == wanted_rarity]
            if category:
                wanted_category = self._skin_key(category)
                rows = [r for r in rows if self._skin_key(self._skin_category_label(r)) == wanted_category]
            if not rows:
                scope = f" per {brawler_name}" if brawler_name else ""
                return f"Non risultano skin in questa categoria{scope}."
            for row in rows:
                row["_owned"] = int(row.get("external_id")) in owned_ids if row.get("external_id") is not None else False
            owned = [r for r in rows if r["_owned"]]
            missing = [r for r in rows if not r["_owned"]]
            name = lambda r: str(r.get("name_it") or r.get("name_en") or "")
            brawler_title = None
            if brawler_name:
                canonical = str(rows[0].get("brawler_name") or brawler_name)
                brawler_rows = self._get("brawlers_catalog", {
                    "select": "name,name_it",
                    "name": f"eq.{canonical}",
                    "limit": "1",
                })
                brawler_title = str((brawler_rows[0].get("name_it") if brawler_rows else None) or canonical).upper()
            if mode == "owned":
                title = brawler_title if brawler_name else (category or rarity or "SKIN").upper()
                return f"{title} — SKIN POSSEDUTE ({len(owned)}/{len(rows)})\n" + (", ".join(name(r) for r in owned) if owned else "Nessuna.")
            if mode == "missing":
                title = brawler_title if brawler_name else (category or rarity or "SKIN").upper()
                return f"{title} — SKIN MANCANTI ({len(missing)}/{len(rows)})\n" + (", ".join(name(r) for r in missing) if missing else "Nessuna: le possiedi tutte.")
            if not brawler_name:
                if rarity or category:
                    label = category or rarity.title()
                    return "\n".join([f"SKIN ACCOUNT — {label}", f"Possedute: {len(owned)}/{len(rows)}", f"Mancanti: {len(missing)}"])
                breakdown = {}
                for row in rows:
                    key = self._skin_category_label(row)
                    data = breakdown.setdefault(key, [0, 0])
                    data[1] += 1
                    if row["_owned"]:
                        data[0] += 1
                lines = [f"SKIN ACCOUNT\nTotale: {len(owned)}/{len(rows)}", ""]
                for key in sorted(breakdown):
                    have,total = breakdown[key]
                    lines.append(f"{key}: {have}/{total} — mancanti {total-have}")
                return "\n".join(lines)
            title = brawler_title or str(rows[0].get("brawler_name") or brawler_name).upper()
            if mode == "count":
                return f"{title} — SKIN ACCOUNT\nPossedute: {len(owned)}/{len(rows)}\nMancanti: {len(missing)}"
            lines = [f"{title} — SKIN ACCOUNT", f"Totale: {len(owned)}/{len(rows)}"]
            groups = {}
            for row in rows:
                groups.setdefault(self._skin_category_label(row), []).append(row)
            for key, group in sorted(groups.items()):
                have = [r for r in group if r["_owned"]]
                miss = [r for r in group if not r["_owned"]]
                lines += ["", f"{key}: {len(have)}/{len(group)}"]
                if have:
                    lines.append("Possedute: " + ", ".join(name(r) for r in have))
                if miss:
                    lines.append("Mancanti: " + ", ".join(name(r) for r in miss))
            return "\n".join(lines)
        except Exception as exc:
            print("ERRORE SKIN ACCOUNT:", repr(exc), flush=True)
            return "Non riesco a leggere la tua Skin Collection in questo momento."

    def is_registered_private_user(self, telegram_user_id):
        """Private bot access is reserved to active registered community members."""
        return self.get_registered_user(telegram_user_id) is not None

    def track_activity(self, message):
        if not self.ready or not message or not message.from_user:
            return
        if getattr(message.chat, "type", None) not in ("group", "supergroup"):
            return
        user = message.from_user
        payload = {
            "chat_id": int(message.chat_id),
            "telegram_user_id": int(user.id),
            "telegram_username": user.username,
            "display_name": user.full_name,
            "last_seen_at": self._now_iso(),
            "is_active": True,
        }
        try:
            self._post(
                "community_members",
                payload,
                params={"on_conflict": "chat_id,telegram_user_id"},
                prefer="resolution=merge-duplicates,return=minimal",
            )
            self._post(
                "community_settings",
                {"chat_id": int(message.chat_id)},
                params={"on_conflict": "chat_id"},
                prefer="resolution=merge-duplicates,return=minimal",
            )
        except Exception as exc:
            print("ERRORE TRACK ATTIVITA:", repr(exc), flush=True)

    def members(self, chat_id, active_only=True):
        params = {
            "select": "*",
            "chat_id": f"eq.{int(chat_id)}",
            "order": "display_name.asc",
        }
        if active_only:
            params["is_active"] = "eq.true"
        try:
            return self._get("community_members", params)
        except Exception as exc:
            print("ERRORE LETTURA MEMBRI:", repr(exc), flush=True)
            return []

    def register_member(self, message, player_tag):
        player = self.player_fetcher(player_tag)
        if not player:
            return None
        user = message.from_user
        payload = {
            "chat_id": int(message.chat_id),
            "telegram_user_id": int(user.id),
            "telegram_username": user.username,
            "display_name": user.full_name,
            "player_tag": player["tag"].replace("#", ""),
            "player_name": player["name"],
            "ranked_current": player.get("ranked_current"),
            "ranked_peak": player.get("ranked_peak"),
            "ranked_current_elo": player.get("ranked_current_elo"),
            "ranked_season_peak": player.get("ranked_season_peak"),
            "ranked_season_peak_elo": player.get("ranked_season_peak_elo"),
            "ranked_career_peak": player.get("ranked_career_peak"),
            "ranked_career_peak_elo": player.get("ranked_career_peak_elo"),
            "player_last_updated_at": self._now_iso(),
            "last_seen_at": self._now_iso(),
            "is_active": True,
        }
        self._post(
            "community_members",
            payload,
            params={"on_conflict": "chat_id,telegram_user_id"},
            prefer="resolution=merge-duplicates,return=minimal",
        )
        if self.snapshot_saver and player.get("trophies") is not None:
            try:
                self.snapshot_saver(player["tag"], player["name"], player["trophies"])
            except Exception as exc:
                print("ERRORE SNAPSHOT REGISTRAZIONE:", repr(exc), flush=True)
        return player

    def update_member_ranked(self, chat_id, user_id, ranked_current=None, ranked_peak=None):
        payload = {}
        if ranked_current is not None:
            payload["ranked_current"] = ranked_current[:80]
        if ranked_peak is not None:
            payload["ranked_peak"] = ranked_peak[:80]

        if payload:
            self._patch(
                "community_members",
                payload,
                params={
                    "chat_id": f"eq.{int(chat_id)}",
                    "telegram_user_id": f"eq.{int(user_id)}",
                },
            )

    def get_member_by_player_tag(self, chat_id, player_tag):
        tag = player_tag.upper().replace("#", "").strip()
        try:
            rows = self._get(
                "community_members",
                {
                    "select": "*",
                    "chat_id": f"eq.{int(chat_id)}",
                    "player_tag": f"eq.{tag}",
                    "limit": 1,
                },
            )
            return rows[0] if rows else None
        except Exception as exc:
            print("ERRORE LETTURA MEMBER TAG:", repr(exc), flush=True)
            return None

    def _member_current_trophies(self, member):
        tag = member.get("player_tag")
        if not tag:
            return None
        player = self.player_fetcher(tag)
        if player and player.get("trophies") is not None:
            try:
                trophies = int(player["trophies"])
                if self.snapshot_saver:
                    try:
                        self.snapshot_saver(
                            player.get("tag") or tag,
                            player.get("name") or member.get("player_name") or tag,
                            trophies,
                        )
                    except Exception as exc:
                        print("ERRORE SNAPSHOT LIVE:", repr(exc), flush=True)
                return trophies
            except Exception:
                pass
        history = self.history_fetcher(tag, days=120)
        if history:
            try:
                return int(history[-1]["trophies"])
            except Exception:
                pass
        return None

    def ranking(self, chat_id, days=7):
        rows = []
        for member in self.members(chat_id):
            tag = member.get("player_tag")
            if not tag:
                continue
            current = self._member_current_trophies(member)
            if current is None:
                continue
            history = self.history_fetcher(tag, days=max(days + 2, 10))
            changes = self.change_calculator(history, current)
            key = {
                0: "today",
                7: "7d",
                15: "15d",
                30: "30d",
            }.get(days, "7d")
            delta = changes.get(key)
            rows.append(
                {
                    "name": member.get("player_name") or member.get("display_name") or tag,
                    "tag": tag,
                    "current": current,
                    "delta": int(delta) if delta is not None else None,
                }
            )
        rows.sort(
            key=lambda x: (
                x["delta"] is not None,
                x["delta"] if x["delta"] is not None else 0,
                x["current"],
            ),
            reverse=True,
        )
        return rows

    def ranked_history_rows(self, player_tag, days=10):
        """Read chronological Ranked ELO history for one registered player."""
        if not self.ready:
            return []
        tag = str(player_tag or "").upper().replace("#", "").strip()
        since = (datetime.now(timezone.utc) - timedelta(days=max(days + 2, 10))).isoformat()
        try:
            rows = self._request(
                "GET", "ranked_history",
                params={
                    "player_tag": f"eq.{tag}",
                    "ranked_current_elo": "not.is.null",
                    "recorded_at": f"gte.{since}",
                    "select": "ranked_current,ranked_current_elo,recorded_at",
                    "order": "recorded_at.asc",
                    "limit": "5000",
                },
            )
            invalid = {"unranked", "unknown", "ranked unknown", "non classificato", "–", "-"}
            return [
                row for row in (rows or [])
                if str(row.get("ranked_current") or "").strip().casefold() not in invalid
            ]
        except Exception as exc:
            print("ERRORE LETTURA STORICO ELO:", repr(exc), flush=True)
            return []

    @staticmethod
    def _ranked_state_at_or_before(history, target):
        selected = None
        for row in history:
            try:
                dt = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
                if dt <= target:
                    selected = {
                        "elo": int(row["ranked_current_elo"]),
                        "rank": row.get("ranked_current"),
                    }
                else:
                    break
            except Exception:
                continue
        return selected

    @staticmethod
    def _current_ranked_season_start(now):
        """Current Ranked season starts on the third Thursday of the month."""
        local = now.astimezone(ROME)
        first = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days_to_thursday = (3 - first.weekday()) % 7
        third_thursday = first + timedelta(days=days_to_thursday + 14)
        if local < third_thursday:
            prev_last = first - timedelta(days=1)
            prev_first = prev_last.replace(day=1)
            days_to_thursday = (3 - prev_first.weekday()) % 7
            third_thursday = prev_first + timedelta(days=days_to_thursday + 14)
        return third_thursday.astimezone(timezone.utc)

    def ranked_elo_ranking(self, chat_id, days=0, club_name=None):
        """Rank current-season ELO movement without counting the monthly reset as a loss."""
        now = datetime.now(timezone.utc)
        start_today = now.astimezone(ROME).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc)
        season_start = self._current_ranked_season_start(now)
        requested_target = start_today if days == 0 else now - timedelta(days=days)
        target = max(requested_target, season_start)
        rows = []
        for member in self.members(chat_id):
            tag = member.get("player_tag")
            if not tag:
                continue
            player = self.player_fetcher(tag)
            if not player:
                continue
            actual_club = self._club_name_from_player(player)
            if club_name and (actual_club or "").casefold() != club_name.casefold():
                continue
            current = player.get("ranked_current_elo")
            if current is None:
                current = member.get("ranked_current_elo")
            try:
                current = int(current)
            except (TypeError, ValueError):
                continue
            history = self.ranked_history_rows(tag, days)
            baseline = self._ranked_state_at_or_before(history, target)
            # If there is no snapshot at/before the boundary, use the first
            # valid snapshot after it. This is essential on the first tracking
            # day of a new Ranked season (and for newly registered players).
            if baseline is None:
                lower_bound = season_start if requested_target < season_start else target
                for row in history:
                    try:
                        dt = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
                        if dt >= lower_bound:
                            baseline = {
                                "elo": int(row["ranked_current_elo"]),
                                "rank": row.get("ranked_current"),
                            }
                            break
                    except Exception:
                        continue
            delta = current - baseline["elo"] if baseline is not None else None
            rows.append({
                "name": player.get("name") or member.get("player_name") or member.get("display_name") or tag,
                "tag": tag,
                "current": current,
                "delta": delta,
                "rank": player.get("ranked_current") or member.get("ranked_current"),
                "previous_rank": (baseline or {}).get("rank"),
            })
        rows.sort(key=lambda x: (
            x["delta"] is not None,
            x["delta"] if x["delta"] is not None else 0,
            x["current"],
        ), reverse=True)
        return rows

    def ranked_elo_ranking_text(self, chat_id, days=0, club_name=None):
        rows = self.ranked_elo_ranking(chat_id, days, club_name)
        scope = club_name or "COMMUNITY"
        period = "OGGI" if days == 0 else f"{days} GIORNI"
        if not rows:
            return f"Nessun ELO Classificata disponibile per {scope}."
        lines = [f"CLASSIFICA {scope} - ELO CLASSIFICATA {period}", ""]
        for index, row in enumerate(rows[:60], 1):
            delta = row["delta"]
            delta_text = (
                ("+" if delta > 0 else "") + self.number_formatter(delta)
                if delta is not None else "storico non ancora disponibile"
            )
            rank_now = row.get("rank") or "Non disponibile"
            rank_before = row.get("previous_rank")
            rank_change = ""
            if rank_before and rank_now and rank_before.casefold() != rank_now.casefold():
                rank_change = f" - {rank_before} ↑ {rank_now}" if (delta or 0) > 0 else f" - {rank_before} ↓ {rank_now}"
            else:
                rank_change = f" - {rank_now}"
            lines.append(
                f"{index}. {row['name']} - {self.number_formatter(row['current'])} ELO"
                f"{rank_change} ({delta_text})"
            )
        return "\n".join(lines)

    CLUB_ALIASES = {
        "titani": "TITANI ABUSIVI", "titani abusivi": "TITANI ABUSIVI",
        "tamarri": "TAMARRI ABUSIVI", "tamarri abusivi": "TAMARRI ABUSIVI",
        "tornadi": "TORNADI ABUSIVI", "tornadi abusivi": "TORNADI ABUSIVI",
        "talenti": "TALENTI ABUSIVI", "talenti abusivi": "TALENTI ABUSIVI",
    }

    # Graphic ranking cards use the matching Brawl Stars stat artwork when available.
    # URLs are resolved at runtime by the card renderer; text output remains the fallback.
    STAT_ICON_KEYS = {
        "trofei": "trophy",
        "brawler": "brawlers",
        "livello": "experience",
        "prestigio": "prestige",
        "3v3": "3v3",
        "solo": "solo",
        "duo": "duo",
        "classificata": "ranked",
        "classificata stagione": "ranked",
        "classificata carriera": "ranked",
    }

    STAT_DEFS = {
        "trofei": ("trophies", "Trofei"),
        "brawler": ("brawlers", "Brawler"),
        "livello": ("level", "Livello account"),
        "prestigio": ("prestige", "Prestigio"),
        "3v3": ("wins_3v3", "Vittorie 3v3"),
        "solo": ("wins_solo", "Vittorie Solo"),
        "duo": ("wins_duo", "Vittorie Duo"),
        "classificata": ("ranked_current_elo", "Classificata attuale"),
        "classificata stagione": ("ranked_season_peak_elo", "Record Classificata stagione"),
        "classificata carriera": ("ranked_career_peak_elo", "Record Classificata carriera"),
    }

    def _club_name_from_player(self, player):
        value=(player or {}).get("club_name") or (player or {}).get("club")
        if isinstance(value, dict): value=value.get("name")
        return str(value).strip() if value else None

    def _stat_rows(self, chat_id, stat_key, club_name=None):
        definition=self.STAT_DEFS.get(stat_key)
        if not definition: return []
        field,_=definition
        rows=[]
        for member in self.members(chat_id):
            tag=member.get("player_tag")
            if not tag: continue
            player=self.player_fetcher(tag)
            if not player: continue
            normalized_tag=str(player.get("tag") or tag).upper().replace("#", "")
            if normalized_tag == "2VQYLG0RU8":
                player["club"]="TALENTI ABUSIVI"
                player["club_name"]="TALENTI ABUSIVI"
            actual_club=self._club_name_from_player(player)
            if club_name and (actual_club or "").casefold()!=club_name.casefold(): continue
            value=player.get(field)
            if value is None: continue
            try: value=int(value)
            except (TypeError,ValueError): continue
            display_value=None
            if stat_key == "classificata": display_value=player.get("ranked_current") or member.get("ranked_current")
            elif stat_key == "classificata stagione": display_value=player.get("ranked_season_peak") or member.get("ranked_season_peak")
            elif stat_key == "classificata carriera": display_value=player.get("ranked_career_peak") or player.get("ranked_peak") or member.get("ranked_career_peak") or member.get("ranked_peak")
            rows.append({"name":player.get("name") or member.get("player_name") or member.get("display_name") or tag,"value":value,"display_value":display_value,"tag":tag,"icon_url":player.get("icon_url")})
        rows.sort(key=lambda x:x["value"],reverse=True)
        return rows

    def stat_ranking_text(self, chat_id, stat_key, club_name=None):
        definition=self.STAT_DEFS.get(stat_key)
        if not definition: return "Statistica non riconosciuta."
        _,label=definition
        rows=self._stat_rows(chat_id,stat_key,club_name)
        scope=club_name or "COMMUNITY"
        if not rows: return f"Nessun dato disponibile per {label} in {scope}."
        lines=[f"CLASSIFICA {scope} - {label.upper()}",""]
        for i,row in enumerate(rows[:60],1):
            value = self.number_formatter(row['value'])
            if stat_key.startswith("classificata") and row.get("display_value"):
                value = f"{value} ELO - {row['display_value']}"
            lines.append(f"{i}. {row['name']} - {value}")
        return "\n".join(lines)

    def all_stats_text(self, chat_id, club_name=None):
        scope=club_name or "COMMUNITY"
        lines=[f"STATISTICHE E CLASSIFICHE - {scope}",""]
        for key,(_,label) in self.STAT_DEFS.items():
            rows=self._stat_rows(chat_id,key,club_name)
            if rows: lines.append(f"{label}: 1° {rows[0]['name']} - {self.number_formatter(rows[0]['value'])}")
        lines += ["","Classifiche disponibili: trofei, Brawler, livello, prestigio, 3v3, Solo, Duo, Classificata, Classificata stagione, Classificata carriera."]
        return "\n".join(lines)

    def wins_3v3_ranking_text(self, chat_id):
        rows = []
        for member in self.members(chat_id):
            tag = member.get("player_tag")
            if not tag:
                continue
            player = self.player_fetcher(tag)
            if not player or player.get("wins_3v3") is None:
                continue
            try:
                wins = int(player["wins_3v3"])
            except (TypeError, ValueError):
                continue
            rows.append({
                "name": player.get("name") or member.get("player_name") or member.get("display_name") or tag,
                "wins": wins,
            })
        rows.sort(key=lambda row: row["wins"], reverse=True)
        if not rows:
            return "Non riesco a recuperare le vittorie 3v3 dei giocatori registrati in questo momento."
        lines = ["CLASSIFICA COMMUNITY - VITTORIE 3V3", ""]
        for index, row in enumerate(rows[:60], 1):
            lines.append(
                f"{index}. {row['name']} - {self.number_formatter(row['wins'])} vittorie"
            )
        return "\n".join(lines)

    def ranking_text(self, chat_id, days=7):
        rows = self.ranking(chat_id, days)
        if not rows:
            return (
                "Non ho ancora abbastanza giocatori registrati/storico trofei. "
                "Ogni membro può usare: registrami #TAG"
            )
        period_label = "OGGI" if days == 0 else f"{days} GIORNI"
        lines = [f"CLASSIFICA COMMUNITY - {period_label}", ""]
        for index, row in enumerate(rows[:60], 1):
            if row["delta"] is None:
                delta_text = "storico di oggi non disponibile" if days == 0 else f"storico {days}g non ancora disponibile"
            else:
                sign = "+" if row["delta"] > 0 else ""
                delta_text = f"{sign}{row['delta']}"
            lines.append(
                f"{index}. {row['name']} - {self.number_formatter(row['current'])} "
                f"({delta_text})"
            )
        return "\n".join(lines)

    def club_summary_text(self, chat_id):
        members = self.members(chat_id)
        registered = [m for m in members if m.get("player_tag")]
        ranking7 = self.ranking(chat_id, 7)
        total = sum(r["current"] for r in ranking7)
        valid_growth = [r for r in ranking7 if r["delta"] is not None]
        growth7 = sum(r["delta"] for r in valid_growth)
        top = valid_growth[:3]
        lines = [
            "COMMUNITY ABUSIVI - PROFILO CLUB",
            "Ordine club: TITANI ABUSIVI > TAMARRI ABUSIVI > TORNADI ABUSIVI > TALENTI ABUSIVI",
            "",
            f"Membri Telegram tracciati: {len(members)}",
            f"Giocatori registrati: {len(registered)}",
        ]
        if ranking7:
            lines.extend(
                [
                    f"Trofei registrati complessivi: {self.number_formatter(total)}",
                    (f"Crescita complessiva 7 giorni: {'+' if growth7 > 0 else ''}{growth7}" if valid_growth else "Crescita complessiva 7 giorni: storico non ancora disponibile"),
                    "",
                    "Top crescita 7 giorni:" if valid_growth else "Top crescita 7 giorni: storico non ancora disponibile",
                ]
            )
            for row in top:
                lines.append(f"- {row['name']}: {'+' if row['delta'] > 0 else ''}{row['delta']}")
        else:
            lines.append("Storico trofei ancora insufficiente per il riepilogo competitivo.")
        return "\n".join(lines)

    def registered_members_text(self, chat_id):
        """Return the linked accounts for this chat, without exposing Telegram IDs."""
        registered = [member for member in self.members(chat_id) if member.get("player_tag")]
        lines = ["TITANI ABUSIVI - ACCOUNT REGISTRATI", ""]
        if not registered:
            lines.append("Nessun membro ha ancora collegato un account con registrami #TAG.")
            return "\n".join(lines)
        lines.append(f"Account collegati: {len(registered)}")
        for index, member in enumerate(registered, 1):
            telegram_name = member.get("display_name") or member.get("telegram_username") or "Membro"
            player_name = member.get("player_name") or "Nome non disponibile"
            tag = str(member.get("player_tag") or "").lstrip("#")
            lines.append(f"{index}. {telegram_name} → {player_name} (#{tag})")
        return "\n".join(lines)

    def _parse_dt(self, value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None

    def inactivity_rows(self, chat_id):
        settings = self.get_settings(chat_id)
        warn_days = int(settings.get("inactivity_warn_days", 5))
        kick_days = int(settings.get("inactivity_kick_days", 10))
        now = datetime.now(timezone.utc)
        rows = []
        for member in self.members(chat_id):
            last_seen = self._parse_dt(member.get("last_seen_at"))
            if not last_seen:
                continue
            vacation_until = self._parse_dt(member.get("vacation_until"))
            if vacation_until and vacation_until > now:
                continue
            days = int((now - last_seen).total_seconds() // 86400)
            if days >= warn_days:
                rows.append((member, days, days >= kick_days))
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows

    def inactivity_text(self, chat_id):
        settings = self.get_settings(chat_id)
        rows = self.inactivity_rows(chat_id)
        if not rows:
            return "Nessun membro tracciato supera attualmente la soglia di inattività."
        lines = [
            "REPORT INATTIVITA TELEGRAM",
            f"Avviso: {settings['inactivity_warn_days']} giorni | Kick: {settings['inactivity_kick_days']} giorni",
            "",
        ]
        for member, days, kick_risk in rows[:30]:
            name = member.get("display_name") or member.get("telegram_username") or str(member.get("telegram_user_id"))
            status = "RISCHIO KICK" if kick_risk else "AVVISO"
            lines.append(f"- {name}: {days} giorni - {status}")
        lines.append("\nNota: il bot misura l'ultima attività vista nel gruppo, non l'ultimo accesso privato a Telegram.")
        return "\n".join(lines)

    def set_vacation(self, chat_id, user_id, days):
        until = datetime.now(timezone.utc) + timedelta(days=days)
        self._patch(
            "community_members",
            {"vacation_until": until.isoformat()},
            params={
                "chat_id": f"eq.{int(chat_id)}",
                "telegram_user_id": f"eq.{int(user_id)}",
            },
        )
        return until

    def get_settings(self, chat_id):
        defaults = {
            "chat_id": int(chat_id),
            "inactivity_warn_days": 5,
            "inactivity_kick_days": 10,
            "auto_kick": False,
            "daily_report_enabled": False,
            "weekly_report_enabled": False,
            "report_hour": 9,
            "last_daily_report_date": None,
            "last_weekly_report_key": None,
        }
        try:
            rows = self._get(
                "community_settings",
                {"select": "*", "chat_id": f"eq.{int(chat_id)}", "limit": 1},
            )
            if rows:
                defaults.update(rows[0])
        except Exception as exc:
            print("ERRORE SETTINGS:", repr(exc), flush=True)
        return defaults

    def set_settings(self, chat_id, **values):
        payload = {"chat_id": int(chat_id), **values, "updated_at": self._now_iso()}
        return self._post(
            "community_settings",
            payload,
            params={"on_conflict": "chat_id"},
            prefer="resolution=merge-duplicates,return=representation",
        )

    async def is_admin(self, context, chat_id, user_id):
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            return member.status in ("administrator", "creator")
        except Exception:
            return False

    def create_event(self, chat_id, user_id, name, when_text, mandatory=False):
        when_text = when_text.strip()
        parsed = None
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(when_text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError("Formato data non valido")
        local_dt = parsed.replace(tzinfo=ROME)
        utc_dt = local_dt.astimezone(timezone.utc)
        rows = self._post(
            "community_events",
            {
                "chat_id": int(chat_id),
                "name": name.strip(),
                "event_at": utc_dt.isoformat(),
                "mandatory": bool(mandatory),
                "status": "open",
                "created_by": int(user_id),
            },
        )
        return rows[0] if rows else None

    def open_events(self, chat_id):
        try:
            return self._get(
                "community_events",
                {
                    "select": "*",
                    "chat_id": f"eq.{int(chat_id)}",
                    "status": "eq.open",
                    "event_at": f"gte.{self._now_iso()}",
                    "order": "event_at.asc",
                },
            )
        except Exception as exc:
            print("ERRORE EVENTI:", repr(exc), flush=True)
            return []

    def events_text(self, chat_id):
        events = self.open_events(chat_id)
        if not events:
            return "Non ci sono eventi aperti."
        lines = ["EVENTI TITANI ABUSIVI", ""]
        for event in events[:10]:
            dt = self._parse_dt(event.get("event_at"))
            local = dt.astimezone(ROME).strftime("%d/%m/%Y %H:%M") if dt else "Data non disponibile"
            mandatory = " - OBBLIGATORIO" if event.get("mandatory") else ""
            lines.append(f"ID {event['id']} - {event['name']} - {local}{mandatory}")
        lines.append("\nPer confermare: partecipo ID")
        return "\n".join(lines)

    def confirm_event(self, event_id, user_id):
        rows = self._get("community_events", {"select": "id", "id": f"eq.{int(event_id)}", "status": "eq.open", "limit": 1})
        if not rows:
            return False
        self._post(
            "community_event_participants",
            {
                "event_id": int(event_id),
                "telegram_user_id": int(user_id),
                "status": "confirmed",
                "updated_at": self._now_iso(),
            },
            params={"on_conflict": "event_id,telegram_user_id"},
            prefer="resolution=merge-duplicates,return=minimal",
        )
        return True

    def save_recruitment(self, chat_id, user_id, username, display_name, player_tag, ranked, notes):
        return self._post(
            "community_recruitments",
            {
                "chat_id": int(chat_id),
                "telegram_user_id": int(user_id),
                "telegram_username": username,
                "display_name": display_name,
                "player_tag": player_tag.replace("#", "").upper(),
                "ranked": ranked,
                "notes": notes,
                "status": "pending",
            },
        )

    def recruitments_text(self, chat_id):
        rows = self._get(
            "community_recruitments",
            {
                "select": "*",
                "chat_id": f"eq.{int(chat_id)}",
                "status": "eq.pending",
                "order": "created_at.desc",
                "limit": 20,
            },
        )
        if not rows:
            return "Nessuna candidatura pendente."
        lines = ["CANDIDATURE PENDENTI", ""]
        for row in rows:
            name = row.get("display_name") or row.get("telegram_username") or str(row.get("telegram_user_id"))
            lines.append(
                f"- {name} | #{row.get('player_tag')} | Ranked: {row.get('ranked') or 'n/d'} | {row.get('notes') or '-'}"
            )
        return "\n".join(lines)

    async def continue_registration(self, message, context):
        stage = context.user_data.get("registration_stage")
        if not stage:
            return False

        text = (message.text or "").strip()

        if stage == "ranked_current":
            context.user_data["registration_ranked_current"] = text[:80]
            context.user_data["registration_stage"] = "ranked_peak"
            await message.reply_text("Qual è il Ranked massima che hai raggiunto?")
            return True

        if stage == "ranked_peak":
            ranked_current = context.user_data.get("registration_ranked_current", "")
            ranked_peak = text[:80]
            self.update_member_ranked(
                message.chat_id,
                message.from_user.id,
                ranked_current=ranked_current,
                ranked_peak=ranked_peak,
            )
            context.user_data.pop("registration_stage", None)
            context.user_data.pop("registration_ranked_current", None)
            await message.reply_text(
                f"Registrazione completata. Ranked attuale: {ranked_current} | Ranked massima: {ranked_peak}"
            )
            return True

        return False

    async def continue_recruitment(self, message, context):
        stage = context.user_data.get("recruitment_stage")
        if not stage:
            return False
        text = (message.text or "").strip()
        if stage == "tag":
            match = re.search(r"#?([0289PYLQGRJCUV]{3,15})", text, re.I)
            if not match:
                await message.reply_text("Tag non valido. Inviami il tuo tag Brawl Stars, per esempio #ABC123.")
                return True
            context.user_data["recruitment_tag"] = match.group(1).upper()
            context.user_data["recruitment_stage"] = "ranked"
            await message.reply_text("Qual è il tuo livello Ranked attuale?")
            return True
        if stage == "ranked":
            context.user_data["recruitment_ranked"] = text[:80]
            context.user_data["recruitment_stage"] = "notes"
            await message.reply_text("Ultima cosa: scrivi eventuali note (orari, obiettivi, esperienza) oppure rispondi 'nessuna'.")
            return True
        if stage == "notes":
            tag = context.user_data.get("recruitment_tag", "")
            ranked = context.user_data.get("recruitment_ranked", "")
            notes = "" if text.lower() == "nessuna" else text[:500]
            self.save_recruitment(
                message.chat_id,
                message.from_user.id,
                message.from_user.username,
                message.from_user.full_name,
                tag,
                ranked,
                notes,
            )
            for key in ("recruitment_stage", "recruitment_tag", "recruitment_ranked"):
                context.user_data.pop(key, None)
            await message.reply_text("Candidatura registrata. La direzione potrà consultarla dal bot.")
            return True
        return False

    def operational_report_text(self, chat_id, period="weekly"):
        members = self.members(chat_id)
        ranking = self.ranking(chat_id, 7)
        inactive = self.inactivity_rows(chat_id)
        valid_growth = [x for x in ranking if x["delta"] is not None]
        growth = sum(x["delta"] for x in valid_growth)
        title = "REPORT SETTIMANALE" if period == "weekly" else "REPORT GIORNALIERO"
        lines = [
            f"TITANI ABUSIVI - {title}",
            "",
            f"Membri tracciati: {len(members)}",
            f"Giocatori registrati: {sum(1 for m in members if m.get('player_tag'))}",
            (f"Crescita trofei (7 giorni): {'+' if growth > 0 else ''}{growth}" if valid_growth else "Crescita trofei (7 giorni): storico non ancora disponibile"),
            f"Membri sopra soglia inattività: {len(inactive)}",
        ]
        if valid_growth:
            lines.append("\nTop crescita:")
            for row in valid_growth[:5]:
                lines.append(f"- {row['name']}: {'+' if row['delta'] > 0 else ''}{row['delta']}")
        if inactive:
            lines.append("\nDa controllare:")
            for member, inactive_days, risk in inactive[:8]:
                name = member.get("display_name") or member.get("telegram_username") or str(member.get("telegram_user_id"))
                lines.append(f"- {name}: {inactive_days} giorni{' - RISCHIO KICK' if risk else ''}")
        return "\n".join(lines)

    async def handle_command(self, message, context, question):
        q_skin = question.strip()
        registered = context.user_data.get("_registered_user") or self.get_registered_user(message.from_user.id)

        # Skin Account: totals, category queries, owned/missing lists and per-Brawler details.
        category_rx = r"(rare|super\\s+rare|epiche|mitiche|leggendarie|(?:skin\\s+)?overdrive|ipercharge|collezione|collector|pass\\s+pro|brawl\\s+pass|argento|oro(?:\\s+24\\s+carati)?|speciali|senza\\s+rarit[àa])"
        rarity_aliases = {
            "rare":"Rare", "super rare":"Super rare", "epiche":"Epiche", "mitiche":"Mitiche",
            "leggendarie":"Leggendarie", "overdrive":"Skin Overdrive", "skin overdrive":"Skin Overdrive",
            "ipercharge":"Skin Overdrive", "collezione":"Collezione", "collector":"Collezione",
            "pass pro":"Pass Pro", "brawl pass":"Brawl Pass", "argento":"Argento",
            "oro":"Oro 24 carati", "oro 24 carati":"Oro 24 carati", "speciali":"Senza rarità",
            "senza rarità":"Senza rarità", "senza rarita":"Senza rarità",
        }
        cat = lambda raw: rarity_aliases[re.sub(r"\\s+", " ", raw.lower()).strip()]

        skin_chart_q = re.fullmatch(r"(?:fammi\\s+)?grafico\\s+skin(?:\\s+"+category_rx+r")?(?:\\s+(7|15|30|60|90|180|365)(?:\\s+giorni)?)?", q_skin, re.I)
        if skin_chart_q:
            chart_category = cat(skin_chart_q.group(1)) if skin_chart_q.group(1) else "Totale"
            chart_days = int(skin_chart_q.group(2) or 30)
            chart, error = self.skin_history_chart(registered, category=chart_category, days=chart_days)
            if error:
                await message.reply_text(error)
            else:
                await message.reply_photo(photo=chart, caption=f"Skin Account — {chart_category} — ultimi {chart_days} giorni")
            return True

        skin_image_q = re.fullmatch(
            r"(?:mostrami|fammi\\s+vedere|immagine(?:\\s+di)?|foto(?:\\s+di)?)\\s+(?:la\\s+skin\\s+)?(.+?)\\s+(?:di|del|della)\\s+(.+)",
            q_skin, re.I,
        )
        category_brawler_list_q = re.fullmatch(r"quali\\s+skin\\s+"+category_rx+r"\\s+(?:di|del|della)\\s+(.+?)\\s+(mi\\s+mancano|ho|possiedo)", q_skin, re.I)
        category_list_q = re.fullmatch(r"quali\\s+skin\\s+"+category_rx+r"\\s+(mi\\s+mancano|ho|possiedo)", q_skin, re.I)
        category_brawler_count_q = re.fullmatch(r"quante\\s+skin\\s+"+category_rx+r"\\s+(?:ha|di|del|della)\\s+(.+)", q_skin, re.I)
        category_count_q = re.fullmatch(r"quante\\s+skin\\s+"+category_rx+r"(?:\\s+(?:ho|possiedo))?", q_skin, re.I)
        missing_brawler_q = re.fullmatch(r"(?:quali\\s+)?skin\\s+(?:di|del|della)\\s+(.+?)\\s+(?:mi\\s+)?mancano", q_skin, re.I) or re.fullmatch(r"(?:quali\\s+)?skin\\s+(?:mi\\s+)?mancano\\s+(?:di|del|della)\\s+(.+)", q_skin, re.I)
        owned_brawler_q = re.fullmatch(r"(?:quali\\s+)?skin\\s+(?:di|del|della)\\s+(.+?)\\s+(?:ho|possiedo)", q_skin, re.I)
        account_brawler_q = re.fullmatch(r"(?:fammi\\s+)?skin\\s+account\\s+(?:di\\s+)?(.+)", q_skin, re.I)
        skin_brawler_count_q = re.fullmatch(r"quante\\s+skin\\s+(?:ha|di|del|della)\\s+(.+)", q_skin, re.I)
        skin_all_q = re.fullmatch(r"(?:quante\\s+)?skin(?:\\s+(?:ho|possiedo))?", q_skin, re.I)

        answer = None
        if skin_image_q:
            result = await self.send_skin_image(message, skin_image_q.group(2).strip(), skin_image_q.group(1).strip())
            if result is not True:
                await message.reply_text(result)
            return True
        if category_brawler_list_q:
            mode = "missing" if "mancano" in category_brawler_list_q.group(3).lower() else "owned"
            answer = self.skin_account_text(registered, brawler_name=category_brawler_list_q.group(2).strip(), category=cat(category_brawler_list_q.group(1)), mode=mode)
        elif category_list_q:
            mode = "missing" if "mancano" in category_list_q.group(2).lower() else "owned"
            answer = self.skin_account_text(registered, category=cat(category_list_q.group(1)), mode=mode)
        elif category_brawler_count_q:
            answer = self.skin_account_text(registered, brawler_name=category_brawler_count_q.group(2).strip(), category=cat(category_brawler_count_q.group(1)), mode="count")
        elif category_count_q:
            answer = self.skin_account_text(registered, category=cat(category_count_q.group(1)), mode="count")
        elif missing_brawler_q:
            answer = self.skin_account_text(registered, brawler_name=missing_brawler_q.group(1).strip(), mode="missing")
        elif owned_brawler_q:
            answer = self.skin_account_text(registered, brawler_name=owned_brawler_q.group(1).strip(), mode="owned")
        elif account_brawler_q:
            answer = self.skin_account_text(registered, brawler_name=account_brawler_q.group(1).strip(), mode="full")
        elif skin_brawler_count_q:
            answer = self.skin_account_text(registered, brawler_name=skin_brawler_count_q.group(1).strip(), mode="count")
        elif skin_all_q:
            answer = self.skin_account_text(registered)
        if answer is not None:
            await message.reply_text(answer)
            return True

        q = (question or "").strip()
        q = re.sub(r"^[!/]+", "", q).strip()
        q = q.strip("\"\'“”‘’ ").strip()
        ql = q.lower()

        if ql in ("aiuto", "help", "comandi", "funzioni"):
            await message.reply_text(HELP_TEXT)
            return True

        if ql in ("regole", "faq", "regolamento"):
            await message.reply_text(FAQ_TEXT)
            return True

        ranked_map = re.fullmatch(r"(?:draft\s+ranked|ranked|classificata)\s+(.+)", q, re.I)
        if ranked_map and not re.fullmatch(r"(?:oggi|7|15|30)(?:\s+giorni)?", ranked_map.group(1), re.I):
            raw = ranked_map.group(1).strip()
            rank_aliases = {
                "bronzo i":"Bronzo I","bronzo 1":"Bronzo I","bronze i":"Bronzo I","bronze 1":"Bronzo I",
                "bronzo ii":"Bronzo II","bronzo 2":"Bronzo II","bronze ii":"Bronzo II","bronze 2":"Bronzo II",
                "bronzo iii":"Bronzo III","bronzo 3":"Bronzo III","bronze iii":"Bronzo III","bronze 3":"Bronzo III",
                "argento i":"Argento I","argento 1":"Argento I","silver i":"Argento I","silver 1":"Argento I",
                "argento ii":"Argento II","argento 2":"Argento II","silver ii":"Argento II","silver 2":"Argento II",
                "argento iii":"Argento III","argento 3":"Argento III","silver iii":"Argento III","silver 3":"Argento III",
                "oro i":"Oro I","oro 1":"Oro I","gold i":"Oro I","gold 1":"Oro I",
                "oro ii":"Oro II","oro 2":"Oro II","gold ii":"Oro II","gold 2":"Oro II",
                "oro iii":"Oro III","oro 3":"Oro III","gold iii":"Oro III","gold 3":"Oro III",
                "diamante i":"Diamante I","diamante 1":"Diamante I","diamond i":"Diamante I","diamond 1":"Diamante I",
                "diamante ii":"Diamante II","diamante 2":"Diamante II","diamond ii":"Diamante II","diamond 2":"Diamante II",
                "diamante iii":"Diamante III","diamante 3":"Diamante III","diamond iii":"Diamante III","diamond 3":"Diamante III",
                "mito i":"Mito I","mito 1":"Mito I","mythic i":"Mito I","mythic 1":"Mito I",
                "mito ii":"Mito II","mito 2":"Mito II","mythic ii":"Mito II","mythic 2":"Mito II",
                "mito iii":"Mito III","mito 3":"Mito III","mythic iii":"Mito III","mythic 3":"Mito III",
                "leggendario i":"Leggendario I","leggendario 1":"Leggendario I","legendary i":"Leggendario I","legendary 1":"Leggendario I",
                "leggendario ii":"Leggendario II","leggendario 2":"Leggendario II","legendary ii":"Leggendario II","legendary 2":"Leggendario II",
                "leggendario iii":"Leggendario III","leggendario 3":"Leggendario III","legendary iii":"Leggendario III","legendary 3":"Leggendario III",
                "maestro":"Maestro","masters":"Maestro","master":"Maestro",
            }
            rank_name = None
            map_name = raw
            for alias, canonical in sorted(rank_aliases.items(), key=lambda x: len(x[0]), reverse=True):
                if raw.casefold().endswith(" "+alias) or raw.casefold() == alias:
                    rank_name=canonical
                    map_name=raw[:-len(alias)].strip()
                    break
            if not rank_name:
                # answer() resolves the registered Telegram identity once per request.
                # Reuse it here instead of performing a second community_members query.
                me=context.user_data.get("_registered_user") or {}
                current=me.get("ranked_current")
                elo=me.get("ranked_current_elo")
                if current not in (None,"Non classificato","Unranked"):
                    rank_name=current
                    try:
                        context.user_data["ranked_draft_elo"]=int(elo) if elo is not None else None
                    except (TypeError,ValueError):
                        context.user_data["ranked_draft_elo"]=None
            response = self.draft_map_advice_text(map_name, rank_name=rank_name)
            if response:
                identity=self._draft_identity(map_name)
                draft_format=self._ranked_draft_format(rank_name)
                context.user_data["ranked_draft"] = {"map": identity.get("map_en") if identity else map_name, "map_it": identity.get("map_it") if identity else map_name, "map_id": identity.get("map_id") if identity else None, "mode": identity.get("mode_en") if identity else None, "mode_it": identity.get("mode_it") if identity else None, "rank": rank_name, "elo": context.user_data.pop("ranked_draft_elo", None), "draft_format": draft_format, "first_pick": None, "pick_sequence": [], "my_picks": [], "enemy_picks": [], "bans": []}
                if draft_format == "all_pick":
                    response += "\nFormato: selezione normale, senza ban."
                elif draft_format == "ban_all_pick":
                    response += "\nFormato: 6 ban totali (3+3), poi selezione normale senza ordine a turni."
                elif draft_format == "turn_pick":
                    response += "\nFormato: 6 ban totali (3+3), poi 6 pick con ordine 1-2-2-1.\nIndica: primo pick nostro oppure primo pick avversario."
                await message.reply_text(response)
                return True

        draft_state = context.user_data.get("ranked_draft") or {}
        if draft_state and re.fullmatch(r"(?:stato|riepilogo|mostra)\\s+(?:draft|classificata)|draft\\s+(?:stato|riepilogo)", q, re.I):
            lines=[f"DRAFT — {draft_state.get('map_it') or draft_state.get('map') or 'mappa'}"]
            if draft_state.get("mode_it"): lines.append("Modalità: "+str(draft_state["mode_it"]))
            if draft_state.get("rank"):
                rank_line="Ranked: "+str(draft_state["rank"])
                if draft_state.get("elo") is not None: rank_line+=f" — ELO {draft_state['elo']}"
                lines.append(rank_line)
            lines.append("Miei pick: "+(", ".join(draft_state.get("my_picks") or []) or "nessuno"))
            lines.append("Pick avversari: "+(", ".join(draft_state.get("enemy_picks") or []) or "nessuno"))
            lines.append("Ban: "+(", ".join(draft_state.get("bans") or []) or "nessuno"))
            if draft_state.get("draft_format") == "turn_pick":
                turn=self._draft_next_turn_text(draft_state)
                if turn: lines.append(turn)
            comp_advice=self.draft_comp_advice_text(draft_state)
            if comp_advice: lines.append(comp_advice)
            await message.reply_text("\\n".join(lines))
            return True
        if draft_state and re.fullmatch(r"(?:reset|azzera|annulla|chiudi)\\s+(?:draft|classificata)|(?:draft|classificata)\\s+(?:reset|azzera|annulla|chiudi)", q, re.I):
            context.user_data.pop("ranked_draft",None)
            context.user_data.pop("ranked_draft_elo",None)
            await message.reply_text("Draft chiusa. Puoi iniziarne una nuova con: Ranked <nome mappa>.")
            return True
        first_pick_q = re.fullmatch(r"(?:primo\\s+pick|first\\s+pick)\\s+(nostro|mio|squadra|avversario|avversaria|nemico)", q, re.I)
        if draft_state and first_pick_q:
            if draft_state.get("draft_format") != "turn_pick":
                await message.reply_text("L'ordine a turni dei pick si usa da Mito I in poi.")
                return True
            raw_side=first_pick_q.group(1).casefold()
            draft_state["first_pick"]="enemy" if raw_side in ("avversario","avversaria","nemico") else "my"
            draft_state["pick_sequence"]=[]
            draft_state["my_picks"]=[]
            draft_state["enemy_picks"]=[]
            context.user_data["ranked_draft"]=draft_state
            await message.reply_text("Ordine pick impostato. "+self._draft_next_turn_text(draft_state))
            return True

        auto_ban_q = re.fullmatch(r"(?:ban|banna|bannato)\\s+(.+)", q, re.I)
        if draft_state and auto_ban_q:
            draft_format=draft_state.get("draft_format")
            if draft_format == "all_pick":
                await message.reply_text("In questa fascia Ranked non è prevista la fase ban.")
                return True
            bans=draft_state.setdefault("bans",[])
            if len(bans) >= 6:
                await message.reply_text("I 6 ban della Draft sono già completi.")
                return True
            brawler=auto_ban_q.group(1).strip()
            if any(str(x).casefold() == brawler.casefold() for x in bans):
                await message.reply_text(f"{brawler} è già presente nei ban.")
                return True
            bans.append(brawler)
            context.user_data["ranked_draft"]=draft_state
            body=f"Ban registrato: {brawler}. Ban {len(bans)}/6."
            if len(bans) == 6:
                if draft_format == "turn_pick":
                    body+="\\nBan completati. "+self._draft_next_turn_text(draft_state)
                else:
                    body+="\\nBan completati. Puoi procedere con le selezioni."
            await message.reply_text(body)
            return True

        auto_pick_q = re.fullmatch(r"(?:pick|scelto|prende)\\s+(.+)", q, re.I)
        if draft_state and auto_pick_q and draft_state.get("draft_format") == "turn_pick":
            if draft_state.get("first_pick") not in ("my","enemy"):
                await message.reply_text("Prima indica chi ha il primo pick: primo pick nostro oppure primo pick avversario.")
                return True
            seq=draft_state.setdefault("pick_sequence",[])
            if len(seq) >= 6:
                await message.reply_text("I 6 pick della Draft sono già completi.")
                return True
            brawler=auto_pick_q.group(1).strip()
            side=self._draft_pick_order(draft_state["first_pick"])[len(seq)]
            seq.append({"side":side,"brawler":brawler})
            target="my_picks" if side == "my" else "enemy_picks"
            draft_state.setdefault(target,[]).append(brawler)
            context.user_data["ranked_draft"]=draft_state
            body=("Pick registrato per la NOSTRA SQUADRA: " if side == "my" else "Pick registrato per l'AVVERSARIO: ")+brawler+"."
            nxt=self._draft_next_turn_text(draft_state)
            if nxt: body+="\n"+nxt
            if side == "enemy":
                counter=self.brawler_counter_text(brawler,map_name=draft_state.get("map")) or self.brawler_counter_text(brawler)
                if counter: body+="\n"+counter
            comp=self.draft_comp_advice_text(draft_state)
            if comp: body+="\n"+comp
            await message.reply_text(body)
            return True

        # Stateful Draft accepts natural follow-ups without forcing one exact phrase.
        # Keep parsing conservative: only explicit pick/ban wording mutates the state.
        draft_actions = re.findall(
            r"(?:(mio|io)\\s+(?:pick|prendo)|(?:mio\\s+pick|pick\\s+mio))\\s+([^,;]+?)(?=\\s+(?:pick\\s+)?avversari[oa]\\b|\\s+avversari[oa]\\s+(?:pick|prende)\\b|$)"
            r"|(?:(?:pick\\s+)?avversari[oa]\\s+(?:pick|prende)?|avversari[oa]\\s+(?:pick|prende))\\s+([^,;]+?)(?=\\s+(?:mio|io)\\s+(?:pick|prendo)\\b|$)"
            r"|(?:ban|banna|bannato)\\s+([^,;]+)",
            q, re.I,
        )
        if draft_actions and draft_state:
            if any(action[3] for action in draft_actions):
                draft_format=draft_state.get("draft_format")
                if draft_format == "all_pick":
                    await message.reply_text("In questa fascia Ranked non è prevista la fase ban.")
                    return True
                current_bans=draft_state.setdefault("bans",[])
                incoming=[action[3].strip() for action in draft_actions if action[3]]
                room=max(0,6-len(current_bans))
                if not room:
                    await message.reply_text("I 6 ban della Draft sono già completi.")
                    return True
                if len(incoming) > room:
                    await message.reply_text(f"Puoi registrare ancora solo {room} ban: il totale massimo è 6.")
                    return True
            mine=[]; enemy=[]; bans=[]
            for action in draft_actions:
                if action[1]: mine.append(action[1].strip())
                if action[2]: enemy.append(action[2].strip())
                if action[3]: bans.append(action[3].strip())
            draft_state.setdefault("my_picks",[]).extend(x for x in mine if x)
            draft_state.setdefault("enemy_picks",[]).extend(x for x in enemy if x)
            draft_state.setdefault("bans",[]).extend(x for x in bans if x)
            context.user_data["ranked_draft"]=draft_state
            counter_response=None
            if enemy:
                counter_response=self.brawler_counter_text(enemy[-1], map_name=draft_state.get("map"))
                if not counter_response: counter_response=self.brawler_counter_text(enemy[-1])
            summary=[]
            if mine: summary.append("Miei pick: "+", ".join(draft_state["my_picks"]))
            if enemy: summary.append("Pick avversari: "+", ".join(draft_state["enemy_picks"]))
            if bans: summary.append("Ban: "+", ".join(draft_state["bans"]))
            comp_advice=self.draft_comp_advice_text(draft_state)
            body="Draft aggiornato. "+(" | ".join(summary) if summary else "Inserisci il prossimo pick.")
            if comp_advice: body+="\n"+comp_advice
            if counter_response: body+="\n"+counter_response
            await message.reply_text(body)
            return True

        counter_match = re.fullmatch(r"(?:counter(?:\s+di)?|chi\s+countera)\s+(.+)", q, re.I)
        if counter_match:
            response = self.brawler_counter_text(counter_match.group(1))
            if response:
                await message.reply_text(response)
                return True

        if ql in ("sito", "website", "web", "titaniabusivi.it"):
            await message.reply_text(
                "Sito ufficiale della community:\n"
                "https://www.titaniabusivi.it\n\n"
                "Qui trovi tutte le informazioni sulla community, i link ai nostri social ufficiali "
                "e l'accesso a Discord, utilizzato per le vocali della community."
            )
            return True

        if ql in ("discord", "server discord", "vocale", "vocali"):
            await message.reply_text(
                "Server Discord ufficiale della community:\n"
                "https://discord.gg/uwrUfsEaBc\n\n"
                "Usiamo Discord per le vocali della community."
            )
            return True

        match = re.fullmatch(r"(?:registrami|tegistrami)\s*#?([A-Z0-9]{3,15})", q, re.I)
        if match:
            raw_tag = match.group(1).upper()
            if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", raw_tag):
                await message.reply_text(
                    "Tag Brawl Stars non valido. Controllalo e riprova con: registrami #TAG. "
                    "Attenzione: nei tag Brawl Stars la lettera O non è valida; potrebbe essere uno zero (0)."
                )
                return True
            try:
                player = self.register_member(message, raw_tag)
                if not player:
                    await message.reply_text("Non riesco a trovare quel giocatore. Controlla il tag.")
                else:
                    ranked_current = player.get("ranked_current")
                    ranked_peak = player.get("ranked_peak")
                    ranked_season_peak = player.get("ranked_season_peak")
                    club_name = player.get("club_name") or "Senza club / non disponibile"

                    if ranked_current and ranked_peak:
                        await message.reply_text(
                            f"Account collegato: {player['name']} {player['tag']} - "
                            f"{self.number_formatter(player['trophies'])} trofei.\n"
                            f"Club: {club_name}\n"
                            f"Ranked attuale: {ranked_current}\n"
                            f"Ranked massima raggiunta nella stagione: {ranked_season_peak or 'Non disponibile'}\n"
                            f"Ranked massima raggiunta in carriera: {ranked_peak}"
                        )
                    else:
                        context.user_data["registration_stage"] = "ranked_current"
                        await message.reply_text(
                            f"Account collegato: {player['name']} {player['tag']} - "
                            f"{self.number_formatter(player['trophies'])} trofei.\n"
                            f"Club: {club_name}\n\n"
                            f"Qual è il tuo livello Ranked attuale?"
                        )
            except Exception as exc:
                print("ERRORE REGISTRAZIONE:", repr(exc), flush=True)
                await message.reply_text("Non riesco a salvare la registrazione. Verifica che lo schema community sia stato creato su Supabase.")
            return True

        stat_aliases = {
            "3v3":"3v3", "vittorie 3v3":"3v3", "solo":"solo", "vittorie solo":"solo",
            "duo":"duo", "vittorie duo":"duo", "trofei":"trofei", "coppe":"trofei",
            "brawler":"brawler", "brawlers":"brawler", "livello":"livello", "livello account":"livello",
            "prestigio":"prestigio",
            "classificata":"classificata", "ranked":"classificata",
            "classificata attuale":"classificata", "ranked attuale":"classificata",
            "classificata stagione":"classificata stagione", "ranked stagione":"classificata stagione",
            "record classificata stagione":"classificata stagione", "record ranked stagione":"classificata stagione",
            "classificata carriera":"classificata carriera", "ranked carriera":"classificata carriera",
            "record classificata carriera":"classificata carriera", "record ranked carriera":"classificata carriera",
        }
        if ql in ("statistiche","stats community","statistiche community","tutte le statistiche","tutte le classifiche"):
            await message.reply_text(self.all_stats_text(message.chat_id)); return True
        club_all=re.fullmatch(r"(?:statistiche|stats|tutte le statistiche|tutte le classifiche)(?:\s+(?:del|dei|di))?\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)",q,re.I)
        if club_all:
            await message.reply_text(self.all_stats_text(message.chat_id,self.CLUB_ALIASES[club_all.group(1).lower()])); return True
        stat=re.fullmatch(r"classific(?:a|he)(?:\s+(?:player|giocatori))?(?:\s+(?:della\s+)?community)?(?:\s+(?:per|di))?\s+(3v3|vittorie 3v3|solo|vittorie solo|duo|vittorie duo|trofei|coppe|brawlers?|livello(?: account)?|prestigio|classificata(?: attuale| stagione| carriera)?|ranked(?: attuale| stagione| carriera)?|record classificata (?:stagione|carriera)|record ranked (?:stagione|carriera))",q,re.I)
        if stat:
            await message.reply_text(self.stat_ranking_text(message.chat_id, stat_aliases[stat.group(1).lower()])); return True
        clubstat=re.fullmatch(r"classific(?:a|he)\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)(?:\s+(?:per|di))?\s+(3v3|vittorie 3v3|solo|vittorie solo|duo|vittorie duo|trofei|coppe|brawlers?|livello(?: account)?|prestigio|classificata(?: attuale| stagione| carriera)?|ranked(?: attuale| stagione| carriera)?|record classificata (?:stagione|carriera)|record ranked (?:stagione|carriera))",q,re.I)
        if clubstat:
            await message.reply_text(self.stat_ranking_text(message.chat_id, stat_aliases[clubstat.group(2).lower()], self.CLUB_ALIASES[clubstat.group(1).lower()])); return True
        if re.search(r"\bclassific(?:a|he)\b",ql) and "3v3" in ql:
            club_name=next((v for k,v in self.CLUB_ALIASES.items() if k in ql),None)
            await message.reply_text(self.stat_ranking_text(message.chat_id, "3v3", club_name)); return True
        ranked_delta = re.fullmatch(
            r"classific(?:a|he)(?:\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?))?\s+(?:elo\s+)?(?:ranked|classificata)(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?",
            q, re.I,
        )
        if ranked_delta:
            club_key = ranked_delta.group(1)
            club_name = self.CLUB_ALIASES.get(club_key.lower()) if club_key else None
            period = ranked_delta.group(2) or "oggi"
            days = 0 if period.lower() == "oggi" else int(period)
            await message.reply_text(self.ranked_elo_ranking_text(message.chat_id, days, club_name))
            return True

        if re.search(r"\bclassific(?:a|he)\b", ql) and re.search(r"\b(?:classificata|ranked)\b", ql):
            club_name=next((v for k,v in self.CLUB_ALIASES.items() if k in ql),None)
            stat_key="classificata carriera" if "carriera" in ql else ("classificata stagione" if "stagione" in ql else "classificata")
            await message.reply_text(self.stat_ranking_text(message.chat_id, stat_key, club_name)); return True

        if ql == "classifica":
            await message.reply_text(
                "CLASSIFICA COMMUNITY\n\n"
                "Scegli il periodo:\n"
                "- classifica oggi\n"
                "- classifica 7\n"
                "- classifica 15\n"
                "- classifica 30"
            )
            return True

        match = re.fullmatch(
            r"classifica(?:\s+(?:della\s+community))?(?:\s+di)?\s+oggi",
            q,
            re.I,
        )
        if match:
            await message.reply_text(self.ranking_text(message.chat_id, 0))
            return True

        match = re.fullmatch(
            r"classifica(?:\s+(?:della\s+community))?\s+(?:(?:degli\s+)?ultimi\s+)?(7|15|30)(?:\s+giorni)?",
            q,
            re.I,
        )
        if match:
            days = int(match.group(1))
            await message.reply_text(self.ranking_text(message.chat_id, days))
            return True

        if ql in ("club", "profilo club", "stato club"):
            await message.reply_text(self.club_summary_text(message.chat_id))
            return True

        if ql in ("elenco registrati", "registrati", "membri registrati", "account registrati"):
            await message.reply_text(self.registered_members_text(message.chat_id))
            return True

        if ql in ("inattivi", "inattivita", "inattività"):
            await message.reply_text(self.inactivity_text(message.chat_id))
            return True

        match = re.fullmatch(r"assenza\s+(\d{1,3})", q, re.I)
        if match:
            days = max(1, min(90, int(match.group(1))))
            until = self.set_vacation(message.chat_id, message.from_user.id, days)
            await message.reply_text(f"Assenza registrata fino al {until.astimezone(ROME).strftime('%d/%m/%Y')}. In quel periodo non sarai segnalato come inattivo.")
            return True

        if ql == "eventi":
            await message.reply_text(self.events_text(message.chat_id))
            return True

        match = re.fullmatch(r"partecipo\s+(\d+)", q, re.I)
        if match:
            ok = self.confirm_event(int(match.group(1)), message.from_user.id)
            await message.reply_text("Partecipazione confermata." if ok else "Evento non trovato o non più aperto.")
            return True

        match = re.fullmatch(r"evento\s+crea\s+(.+?)\s*\|\s*(.+?)(?:\s*\|\s*(obbligatorio))?", q, re.I)
        if match:
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
                return True
            try:
                event = self.create_event(
                    message.chat_id,
                    message.from_user.id,
                    match.group(1),
                    match.group(2),
                    bool(match.group(3)),
                )
                await message.reply_text(f"Evento creato. ID {event['id']}\n{self.events_text(message.chat_id)}")
            except ValueError:
                await message.reply_text("Formato: evento crea NOME | GG/MM/AAAA HH:MM | obbligatorio")
            except Exception as exc:
                print("ERRORE CREAZIONE EVENTO:", repr(exc), flush=True)
                await message.reply_text("Non riesco a creare l'evento. Verifica lo schema Supabase.")
            return True

        if ql == "reclutamento":
            context.user_data["recruitment_stage"] = "tag"
            await message.reply_text("Candidatura TITANI ABUSIVI. Inviami il tuo tag Brawl Stars.")
            return True

        if ql == "candidature":
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
            else:
                await message.reply_text(self.recruitments_text(message.chat_id))
            return True

        if ql == "report":
            await message.reply_text(self.operational_report_text(message.chat_id, "weekly"))
            return True

        match = re.fullmatch(r"report\s+(giornaliero|settimanale)\s+(on|off)", q, re.I)
        if match:
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
                return True
            key = "daily_report_enabled" if match.group(1).lower() == "giornaliero" else "weekly_report_enabled"
            enabled = match.group(2).lower() == "on"
            self.set_settings(message.chat_id, **{key: enabled})
            await message.reply_text(f"Report {match.group(1).lower()} automatico {'attivato' if enabled else 'disattivato'}.")
            return True

        match = re.fullmatch(r"autokick\s+(on|off)", q, re.I)
        if match:
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
                return True
            enabled = match.group(1).lower() == "on"
            self.set_settings(message.chat_id, auto_kick=enabled)
            await message.reply_text(f"Auto-kick {'ATTIVO' if enabled else 'disattivato'}. L'auto-kick considera solo l'attività che il bot vede nel gruppo.")
            return True

        match = re.fullmatch(r"soglie\s+inattivit[àa]\s+(\d+)\s+(\d+)", q, re.I)
        if match:
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
                return True
            warn = int(match.group(1))
            kick = int(match.group(2))
            if warn < 1 or kick <= warn:
                await message.reply_text("La soglia kick deve essere maggiore della soglia avviso.")
            else:
                self.set_settings(message.chat_id, inactivity_warn_days=warn, inactivity_kick_days=kick)
                await message.reply_text(f"Soglie aggiornate: avviso {warn} giorni, kick {kick} giorni.")
            return True

        return False

    def skin_history_chart(self, registered_user, category="Totale", days=30):
        """Build a PNG chart from real Skin Account snapshots."""
        if not registered_user or not registered_user.get("id"):
            return None, "Devi prima registrare il tuo tag Brawl Stars."
        try:
            rows = self._get("skin_account_history", {
                "select": "snapshot_date,owned_count,total_count,gained_count",
                "community_member_id": f"eq.{int(registered_user['id'])}",
                "category": f"eq.{category}",
                "order": "snapshot_date.asc",
                "limit": str(max(2, min(int(days or 30), 365))),
            })
            if not rows:
                return None, "Non ci sono ancora dati storici Skin Account per questo account."
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            dates = [datetime.strptime(r["snapshot_date"], "%Y-%m-%d") for r in rows]
            owned = [int(r.get("owned_count") or 0) for r in rows]
            totals = [int(r.get("total_count") or 0) for r in rows]
            fig, ax = plt.subplots(figsize=(10, 5.6))
            ax.plot(dates, owned, marker="o", linewidth=2, label="Possedute")
            ax.plot(dates, totals, marker="o", linewidth=1.5, linestyle="--", label="Totali disponibili")
            for i, (x, y) in enumerate(zip(dates, owned)):
                if i == 0:
                    label = str(y)
                else:
                    gain = max(0, y - owned[i - 1])
                    label = f"{y} (+{gain})"
                ax.annotate(label, (x, y), xytext=(0, 9), textcoords="offset points", ha="center", fontsize=9)
            ax.set_title(f"Skin Account — {category}")
            ax.set_xlabel("Data")
            ax.set_ylabel("Numero di skin")
            ax.grid(True, alpha=0.25)
            ax.legend()
            fig.autofmt_xdate()
            fig.tight_layout()
            output = io.BytesIO()
            fig.savefig(output, format="png", dpi=150)
            plt.close(fig)
            output.seek(0)
            return output, None
        except Exception as exc:
            print("ERRORE GRAFICO SKIN:", repr(exc), flush=True)
            return None, "Non riesco a generare il grafico Skin Account in questo momento."

    def save_daily_skin_snapshots(self, snapshot_date=None):
        """Save one monotonic daily Skin Account snapshot per active registered member."""
        day = snapshot_date or datetime.now(timezone.utc).astimezone(ROME).date().isoformat()
        members = self._get("community_members", {
            "select": "id,player_tag",
            "is_active": "eq.true",
            "player_tag": "not.is.null",
            "order": "id.asc",
        })
        catalog = []
        offset = 0
        while True:
            page = self._get("skins_catalog", {
                "select": "external_id,rarity,source_payload",
                "verification_status": "eq.structured_verified",
                "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
                "limit": "1000",
                "offset": str(offset),
            })
            catalog.extend(page)
            if len(page) < 1000:
                break
            offset += 1000
        totals = {"Totale": len(catalog)}
        for row in catalog:
            label = self._skin_category_label(row)
            totals[label] = totals.get(label, 0) + 1
        saved = 0
        skipped = 0
        for member in members or []:
            member_id = int(member["id"])
            existing = self._get("skin_account_history", {
                "select": "id",
                "community_member_id": f"eq.{member_id}",
                "snapshot_date": f"eq.{day}",
                "category": "eq.Totale",
                "limit": "1",
            })
            if existing:
                skipped += 1
                continue
            try:
                owned_ids = self._official_owned_skin_ids(member["player_tag"])
                current = {"Totale": 0}
                for row in catalog:
                    ext = row.get("external_id")
                    if ext is None or int(ext) not in owned_ids:
                        continue
                    current["Totale"] += 1
                    label = self._skin_category_label(row)
                    current[label] = current.get(label, 0) + 1
                previous_rows = self._get("skin_account_history", {
                    "select": "category,owned_count",
                    "community_member_id": f"eq.{member_id}",
                    "snapshot_date": f"lt.{day}",
                    "order": "snapshot_date.desc",
                    "limit": "50",
                })
                previous = {}
                for row in previous_rows or []:
                    previous.setdefault(str(row.get("category")), int(row.get("owned_count") or 0))
                payload = []
                for category, total in totals.items():
                    observed = int(current.get(category, 0))
                    old = previous.get(category)
                    # Owned skins are monotonic. A lower API observation is treated as incomplete.
                    owned = max(observed, old) if old is not None else observed
                    gained = max(0, owned - old) if old is not None else 0
                    payload.append({
                        "community_member_id": member_id,
                        "player_tag": member["player_tag"],
                        "snapshot_date": day,
                        "category": category,
                        "owned_count": owned,
                        "total_count": max(int(total), owned),
                        "gained_count": gained,
                    })
                self._post("skin_account_history", payload, params={"on_conflict": "community_member_id,snapshot_date,category"}, prefer="resolution=merge-duplicates,return=minimal")
                saved += 1
            except Exception as exc:
                print(f"ERRORE SNAPSHOT SKIN MEMBER {member_id}:", repr(exc), flush=True)
        print(f"SKIN DAILY SNAPSHOT: date={day} saved={saved} skipped={skipped}", flush=True)
        return {"date": day, "saved": saved, "skipped": skipped}

    async def scheduled_jobs(self, context):
        if not self.ready:
            return
        now_utc = datetime.now(timezone.utc)
        now_rome = now_utc.astimezone(ROME)

        try:
            self.save_daily_skin_snapshots(now_rome.date().isoformat())
        except Exception as exc:
            print("ERRORE JOB SNAPSHOT SKIN:", repr(exc), flush=True)

        try:
            events = self._get(
                "community_events",
                {
                    "select": "*",
                    "status": "eq.open",
                    "reminder_sent": "eq.false",
                    "event_at": f"gte.{now_utc.isoformat()}",
                    "order": "event_at.asc",
                },
            )
            for event in events:
                dt = self._parse_dt(event.get("event_at"))
                if not dt or dt - now_utc > timedelta(hours=24):
                    continue
                local = dt.astimezone(ROME).strftime("%d/%m/%Y %H:%M")
                await context.bot.send_message(
                    chat_id=int(event["chat_id"]),
                    text=(
                        f"PROMEMORIA EVENTO\n{event['name']} - {local}"
                        f"{' - OBBLIGATORIO' if event.get('mandatory') else ''}\n"
                        f"Conferma con: partecipo {event['id']}"
                    ),
                )
                self._patch("community_events", {"reminder_sent": True}, params={"id": f"eq.{event['id']}"})
        except Exception as exc:
            print("ERRORE JOB EVENTI:", repr(exc), flush=True)

        try:
            settings_rows = self._get("community_settings", {"select": "*"})
        except Exception as exc:
            print("ERRORE JOB SETTINGS:", repr(exc), flush=True)
            settings_rows = []

        for settings in settings_rows:
            chat_id = int(settings["chat_id"])
            report_hour = int(settings.get("report_hour") or 9)

            if now_rome.hour == report_hour:
                today_key = now_rome.strftime("%Y-%m-%d")
                if settings.get("daily_report_enabled") and settings.get("last_daily_report_date") != today_key:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=self.operational_report_text(chat_id, "daily"))
                        self.set_settings(chat_id, last_daily_report_date=today_key)
                    except Exception as exc:
                        print("ERRORE REPORT GIORNALIERO:", repr(exc), flush=True)

                week_key = f"{now_rome.isocalendar().year}-W{now_rome.isocalendar().week}"
                if now_rome.weekday() == 0 and settings.get("weekly_report_enabled") and settings.get("last_weekly_report_key") != week_key:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=self.operational_report_text(chat_id, "weekly"))
                        self.set_settings(chat_id, last_weekly_report_key=week_key)
                    except Exception as exc:
                        print("ERRORE REPORT SETTIMANALE:", repr(exc), flush=True)

            try:
                inactive = self.inactivity_rows(chat_id)
                warn_days = int(settings.get("inactivity_warn_days") or 5)
                kick_days = int(settings.get("inactivity_kick_days") or 10)
                auto_kick = bool(settings.get("auto_kick"))
                for member, days, risk in inactive:
                    last_warning = self._parse_dt(member.get("last_warning_at"))
                    if days >= warn_days and (not last_warning or now_utc - last_warning >= timedelta(hours=24)):
                        name = member.get("display_name") or member.get("telegram_username") or str(member.get("telegram_user_id"))
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Avviso inattività: {name} risulta inattivo nel gruppo da {days} giorni. Soglia kick: {kick_days} giorni.",
                        )
                        self._patch(
                            "community_members",
                            {"last_warning_at": now_utc.isoformat()},
                            params={
                                "chat_id": f"eq.{chat_id}",
                                "telegram_user_id": f"eq.{int(member['telegram_user_id'])}",
                            },
                        )
                    if auto_kick and risk:
                        user_id = int(member["telegram_user_id"])
                        try:
                            chat_member = await context.bot.get_chat_member(chat_id, user_id)
                            if chat_member.status in ("administrator", "creator"):
                                continue
                            await context.bot.ban_chat_member(chat_id, user_id)
                            await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
                            self._patch(
                                "community_members",
                                {"is_active": False},
                                params={
                                    "chat_id": f"eq.{chat_id}",
                                    "telegram_user_id": f"eq.{user_id}",
                                },
                            )
                            await context.bot.send_message(chat_id=chat_id, text=f"{member.get('display_name') or user_id} rimosso automaticamente per {days} giorni di inattività.")
                        except Exception as exc:
                            print("ERRORE AUTOKICK:", repr(exc), flush=True)
            except Exception as exc:
                print("ERRORE JOB INATTIVITA:", repr(exc), flush=True)