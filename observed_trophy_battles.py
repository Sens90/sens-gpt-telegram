"""Conservative normalization of official Supercell trophy battle evidence."""

import hashlib
from datetime import datetime, timezone

from trophy_economy import classify_trophy_change


def _battle_time_iso(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    for pattern in ("%Y%m%dT%H%M%S.%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _find_player(node, player_tag):
    if isinstance(node, dict):
        if str(node.get("tag") or "").replace("#", "").upper() == player_tag:
            return node
        for value in node.values():
            found = _find_player(value, player_tag)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_player(value, player_tag)
            if found is not None:
                return found
    return None


def observed_battle_rows(player_tag, player_name, payload):
    """Return rows only for battles carrying an explicit trophyChange."""
    clean_tag = str(player_tag or "").replace("#", "").upper()
    items = payload.get("items") if isinstance(payload, dict) else None
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        battle = item.get("battle") if isinstance(item.get("battle"), dict) else {}
        # Challenge progress can expose trophyChange=1 and artificial Brawler
        # values (0, 100, 200...). It is not trophy-ladder economy evidence.
        if battle.get("type") == "challenge":
            continue
        battle_time = _battle_time_iso(item.get("battleTime"))
        if not battle_time:
            continue
        target = _find_player(battle, clean_tag) or {}
        trophy_change = battle.get("trophyChange")
        if trophy_change is None:
            trophy_change = target.get("trophyChange")
        try:
            trophy_change = int(trophy_change)
        except (TypeError, ValueError):
            continue

        brawler = target.get("brawler") if isinstance(target.get("brawler"), dict) else {}
        brawlers = target.get("brawlers") if isinstance(target.get("brawlers"), list) else []
        if not brawler and brawlers and isinstance(brawlers[0], dict):
            brawler = brawlers[0]
        brawler_name = str(brawler.get("name") or "").strip() or None
        try:
            # Consecutive official battle-log records prove that this value is
            # the Brawler trophy count before the recorded battle.
            trophies_before = int(brawler.get("trophies"))
        except (TypeError, ValueError):
            trophies_before = None
        placement = battle.get("rank", battle.get("placement"))
        try:
            placement = int(placement) if placement is not None else None
        except (TypeError, ValueError):
            placement = None
        streak = brawler.get("currentWinStreak", target.get("currentWinStreak"))
        try:
            streak = int(streak) if streak is not None else None
        except (TypeError, ValueError):
            streak = None

        event = item.get("event") if isinstance(item.get("event"), dict) else {}
        mode = battle.get("mode") or event.get("mode")
        identity = "|".join((clean_tag, battle_time, str(event.get("id") or ""), str(mode or ""), str(brawler_name or ""), str(trophy_change)))
        expected_base, observed_extra, bonus_type = classify_trophy_change(
            mode, battle.get("result"), trophies_before, trophy_change, placement
        )
        rows.append({
            "player_tag": clean_tag, "player_name": player_name,
            "battle_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "battle_time": battle_time, "brawler_name": brawler_name,
            "brawler_trophies_before": trophies_before, "mode": mode,
            "result": battle.get("result"), "placement": placement,
            "trophy_change": trophy_change, "expected_base_delta": expected_base,
            "observed_extra": observed_extra, "current_win_streak": streak,
            "bonus_type": bonus_type, "raw_battle": item,
        })
    return rows
