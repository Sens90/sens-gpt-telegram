"""Runtime integration for live BrawlTrack player profiles and deterministic meta."""

import os
import re
import requests

import community_features

_ALLOWED_TAG = re.compile(r"[0289PYLQGRJCUV]{3,15}", re.I)
_BRAWLTRACK_PLAYER = "https://brawltrack.app/api/player/{tag}"


def _clean_tag(value):
    tag = str(value or "").upper().replace("#", "").strip()
    return tag if _ALLOWED_TAG.fullmatch(tag) else None


def _first(data, *keys):
    wanted = {key.casefold() for key in keys}
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() in wanted and item not in (None, "", [], {}):
                    return item
            for item in value.values():
                found = walk(item)
                if found not in (None, "", [], {}): return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found not in (None, "", [], {}): return found
        return None
    return walk(data)


def _number(value):
    if value is None or isinstance(value, bool): return None
    if isinstance(value, (int, float)): return int(value)
    match = re.search(r"-?\d[\d.,]*", str(value))
    if not match: return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits else None


def _label(value):
    if value in (None, "", [], {}): return None
    if isinstance(value, str): return value.strip() or None
    if isinstance(value, (int, float)): return str(value)
    if isinstance(value, dict):
        for key in ("name", "rank", "tier", "division", "label", "title"):
            item = value.get(key)
            if item not in (None, ""): return str(item).strip()
    return None


def _club_name(data):
    club = _first(data, "club", "activeClub", "active_club")
    if isinstance(club, dict):
        return _label(club.get("name") or club.get("clubName") or club.get("club_name"))
    return _label(_first(data, "clubName", "club_name", "activeClubName", "active_club_name"))


def fetch_brawltrack_player(player_tag):
    tag = _clean_tag(player_tag)
    if not tag: return None
    try:
        response = requests.get(_BRAWLTRACK_PLAYER.format(tag=tag), headers={"User-Agent":"SensGPT-TitaniAbusivi/1.0","Accept":"application/json"}, timeout=15)
        if response.status_code != 200:
            print("BRAWLTRACK PLAYER:", tag, "HTTP", response.status_code, flush=True); return None
        data = response.json()
        if not isinstance(data, dict): return None
        name = _label(_first(data, "name", "playerName", "player_name"))
        trophies = _number(_first(data, "trophies", "currentTrophies", "current_trophies"))
        if not name or trophies is None: return None
        player = {
            "name":name,"tag":"#"+tag,"trophies":trophies,
            "brawlers":_number(_first(data,"brawlersCount","brawlerCount","brawlers_count","brawler_count")),
            "level":_number(_first(data,"expLevel","level","accountLevel","account_level")),
            "prestige":_number(_first(data,"prestige","prestigeLevel","prestige_level")),
            "wins_3v3":_number(_first(data,"3vs3Victories","3v3Victories","wins3v3","wins_3v3","victories3v3")),
            "wins_solo":_number(_first(data,"soloVictories","soloWins","wins_solo","solo_wins")),
            "wins_duo":_number(_first(data,"duoVictories","duoWins","wins_duo","duo_wins")),
            "club_name":_club_name(data),
            "ranked_current":_label(_first(data,"rankedCurrent","ranked_current","currentRank","current_rank")),
            "ranked_current_elo":_number(_first(data,"rankedCurrentElo","ranked_current_elo","currentElo","current_elo")),
            "ranked_season_peak":_label(_first(data,"rankedSeasonPeak","ranked_season_peak","seasonPeak","season_peak","bestRankThisSeason")),
            "ranked_season_peak_elo":_number(_first(data,"rankedSeasonPeakElo","ranked_season_peak_elo","seasonPeakElo","season_peak_elo")),
            "ranked_career_peak":_label(_first(data,"rankedCareerPeak","ranked_career_peak","careerPeak","career_peak","highestRank","highest_rank")),
            "ranked_career_peak_elo":_number(_first(data,"rankedCareerPeakElo","ranked_career_peak_elo","careerPeakElo","career_peak_elo","highestElo","highest_elo")),
            "source":"BrawlTrack",
        }
        player["ranked_peak"] = player.get("ranked_career_peak")
        return player
    except Exception as exc:
        print("ERRORE BRAWLTRACK PLAYER:", tag, repr(exc), flush=True); return None


def _merge_live_with_fallback(live, fallback):
    if not live: return fallback
    if not fallback: return live
    merged = dict(fallback)
    for key, value in live.items():
        if value not in (None, "", [], {}): merged[key] = value
    return merged


def _is_direct_meta(question):
    q = re.sub(r"\s+", " ", str(question or "").strip().casefold())
    return bool(re.fullmatch(r"(?:il\s+)?meta(?:\s+attuale|\s+di\s+adesso|\s+ora)?[?!.]*", q))


def _search_brawltrack_meta():
    key = os.environ.get("TAVILY_API_KEY")
    if not key: return {}
    response = requests.post("https://api.tavily.com/search", json={
        "api_key":key,
        "query":"Brawl Stars current meta BrawlTrack brawlers win rate usage star rate site:brawltrack.app/brawlers",
        "search_depth":"advanced","max_results":12,"include_raw_content":True,
        "include_answer":False,"include_domains":["brawltrack.app"]}, timeout=20)
    response.raise_for_status()
    data = response.json()
    print("META DIRECT BRAWLTRACK risultati:", len(data.get("results", [])), flush=True)
    return data


_original_init = community_features.CommunityFeatures.__init__
_original_handle_command = community_features.CommunityFeatures.handle_command


def _patched_init(self, supabase_url, supabase_key, player_fetcher, history_fetcher, change_calculator, number_formatter, change_formatter):
    legacy_fetcher = player_fetcher
    def live_first(tag):
        live = fetch_brawltrack_player(tag); fallback = None
        required = ("brawlers","wins_3v3","ranked_current","ranked_career_peak")
        if live is None or any(live.get(key) is None for key in required):
            try: fallback = legacy_fetcher(tag)
            except Exception as exc: print("ERRORE FALLBACK PLAYER:", repr(exc), flush=True)
        return _merge_live_with_fallback(live, fallback)
    _original_init(self, supabase_url, supabase_key, live_first, history_fetcher, change_calculator, number_formatter, change_formatter)


async def _patched_handle_command(self, message, context, question):
    q = (question or "").strip()
    q = re.sub(r"^[!/]+", "", q).strip().strip("\"'“”‘’ ").strip()

    # HARD ROUTE: this runs inside sitecustomize, which Python always imports.
    # A direct meta request is fully handled here and can never reach Tavily->Gemini in app.py.
    if _is_direct_meta(q):
        try:
            from meta_current import render_current_meta
            await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
            report = render_current_meta(_search_brawltrack_meta(), "both")
            if report:
                print("META DIRECT: risposta deterministica inviata; Gemini BLOCCATO", flush=True)
                await message.reply_text(report)
            else:
                print("META DIRECT: BrawlTrack insufficiente; Gemini BLOCCATO", flush=True)
                await message.reply_text("META ATTUALE\n\nBrawlTrack non restituisce abbastanza statistiche verificabili in questo momento. Non genero percentuali, tier o nomi a intuito. Riprova tra poco.")
        except Exception as exc:
            print("META DIRECT ERRORE; Gemini BLOCCATO:", repr(exc), flush=True)
            await message.reply_text("META ATTUALE\n\nNon riesco a verificare i dati BrawlTrack in questo momento. Per evitare informazioni inventate non genero una tier list generica.")
        return True

    match = re.fullmatch(r"(?:status|stato|statistiche|stats|profilo|scheda)(?:\s+(?:del\s+)?giocatore)?\s*#?([0289PYLQGRJCUV]{3,15})", q, re.I)
    if match:
        tag = match.group(1).upper(); player = self.player_fetcher(tag)
        if not player:
            await message.reply_text("Non riesco a trovare questo giocatore. Controlla che il tag sia corretto e riprova."); return True
        member = self.get_member_by_player_tag(message.chat_id, player["tag"])
        def live_or_saved(live_key, saved_key=None):
            value = player.get(live_key)
            if value not in (None, ""): return value
            return (member or {}).get(saved_key or live_key) or "Non disponibile"
        club = player.get("club_name") or "Senza club / non disponibile"
        lines=[player["name"],f"Tag: {player['tag']}",f"Club: {club}","",f"Trofei: {self.number_formatter(player.get('trophies'))}",f"Brawler: {self.number_formatter(player.get('brawlers'))}",f"Livello: {self.number_formatter(player.get('level'))}",f"Prestigio: {self.number_formatter(player.get('prestige'))}",f"Ranked attuale: {live_or_saved('ranked_current')}",f"Ranked massima raggiunta nella stagione: {live_or_saved('ranked_season_peak')}",f"Ranked massima raggiunta in carriera: {live_or_saved('ranked_career_peak','ranked_peak')}","","Vittorie:",f"- 3v3: {self.number_formatter(player.get('wins_3v3'))}",f"- Solo: {self.number_formatter(player.get('wins_solo'))}",f"- Duo: {self.number_formatter(player.get('wins_duo'))}"]
        await message.reply_text("\n".join(lines)); return True
    return await _original_handle_command(self, message, context, question)


community_features.CommunityFeatures.__init__ = _patched_init
community_features.CommunityFeatures.handle_command = _patched_handle_command
