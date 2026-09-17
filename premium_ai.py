import re
import requests
from datetime import datetime, timezone

MAX_PREMIUM_USERS = 2
OWNER_TELEGRAM_ID = 437136453
ANNA_TELEGRAM_ID = 751665886
GENERATION_MANAGERS = {OWNER_TELEGRAM_ID, ANNA_TELEGRAM_ID}
MAX_GENERATION_CHANGE = 100


def _headers(service_key):
    return {"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json"}


def list_premium_users(supabase_url, service_key):
    if not supabase_url or not service_key:
        raise RuntimeError("Supabase non configurato")
    r = requests.get(f"{supabase_url}/rest/v1/ai_profile_privileged_users", headers=_headers(service_key), params={"select": "telegram_user_id,note,created_at", "order": "created_at.asc"}, timeout=15)
    r.raise_for_status(); return r.json()


def is_premium_user(user_id, supabase_url, service_key):
    if not user_id or not supabase_url or not service_key: return False
    r = requests.get(f"{supabase_url}/rest/v1/ai_profile_privileged_users", headers=_headers(service_key), params={"telegram_user_id": f"eq.{int(user_id)}", "select": "telegram_user_id", "limit": "1"}, timeout=15)
    r.raise_for_status(); return bool(r.json())


def add_premium_user(user_id, note, supabase_url, service_key):
    user_id = int(user_id); current = list_premium_users(supabase_url, service_key)
    if any(int(row["telegram_user_id"]) == user_id for row in current): return "already"
    if len(current) >= MAX_PREMIUM_USERS: return "full"
    r = requests.post(f"{supabase_url}/rest/v1/ai_profile_privileged_users", headers={**_headers(service_key), "Prefer": "return=minimal"}, json={"telegram_user_id": user_id, "note": (note or "Premium AI")[:200]}, timeout=15)
    r.raise_for_status(); return "added"


def remove_premium_user(user_id, supabase_url, service_key):
    r = requests.delete(f"{supabase_url}/rest/v1/ai_profile_privileged_users", headers={**_headers(service_key), "Prefer": "return=minimal"}, params={"telegram_user_id": f"eq.{int(user_id)}"}, timeout=15)
    r.raise_for_status(); return True


def get_permanent_extra(user_id, supabase_url, service_key):
    r = requests.get(f"{supabase_url}/rest/v1/ai_profile_quota_bonus", headers=_headers(service_key), params={"telegram_user_id": f"eq.{int(user_id)}", "select": "permanent_extra", "limit": "1"}, timeout=15)
    r.raise_for_status(); rows = r.json()
    return int(rows[0]["permanent_extra"]) if rows else 0


def change_permanent_extra(user_id, delta, supabase_url, service_key):
    current = get_permanent_extra(user_id, supabase_url, service_key)
    new_value = max(0, current + int(delta))
    r = requests.post(f"{supabase_url}/rest/v1/ai_profile_quota_bonus", params={"on_conflict": "telegram_user_id"}, headers={**_headers(service_key), "Prefer": "resolution=merge-duplicates,return=minimal"}, json={"telegram_user_id": int(user_id), "permanent_extra": new_value, "updated_at": datetime.now(timezone.utc).isoformat()}, timeout=15)
    r.raise_for_status(); return current, new_value


def _is_owner(message):
    return bool(message.from_user and int(message.from_user.id) == OWNER_TELEGRAM_ID)


def _can_manage_generations(message):
    return bool(message.from_user and int(message.from_user.id) in GENERATION_MANAGERS)


def _generation_change(q):
    """Return signed quota delta for e.g. 'aggiungi generazione +3' or 'rimuovi generazione +4'."""
    match = re.fullmatch(r"(aggiungi|rimuovi)\s+generazion(?:e|i)(?:\s+([+-]?\d+))?", q)
    if not match:
        return None
    amount = int(match.group(2) or "1")
    amount = abs(amount)
    if amount < 1 or amount > MAX_GENERATION_CHANGE:
        return "invalid"
    return amount if match.group(1) == "aggiungi" else -amount


async def handle_premium_command(message, context, question, supabase_url, service_key):
    q = " ".join((question or "").casefold().split())
    fixed_commands = {"mio id", "id telegram", "telegram id", "rendimi premium", "rimuovimi premium", "rendi premium", "rimuovi premium", "lista premium", "premium list"}
    generation_delta = _generation_change(q)
    if q not in fixed_commands and generation_delta is None: return False

    if q in {"mio id", "id telegram", "telegram id"}:
        user = message.from_user
        await message.reply_text(f"Il tuo Telegram User ID è: {user.id}\nQuesto è l'ID Telegram numerico, non il tag di Brawl Stars.")
        return True

    # Sens e Anna possono assegnare/rimuovere gli extra permanenti, anche in blocco.
    # Esempi: aggiungi generazione +3 / rimuovi generazione +4.
    if generation_delta is not None:
        if generation_delta == "invalid":
            await message.reply_text(f"Indica un numero da 1 a {MAX_GENERATION_CHANGE}. Esempio: aggiungi generazione +3")
            return True
        if not _can_manage_generations(message):
            await message.reply_text("Questo comando è riservato a Sens e Anna."); return True
        target_message = message.reply_to_message
        target = target_message.from_user if target_message else None
        if not target or target.is_bot:
            await message.reply_text("Rispondi a un messaggio della persona. Esempi: 'aggiungi generazione +3' oppure 'rimuovi generazione +4'."); return True
        display = target.full_name or target.username or str(target.id)
        try:
            old, new = change_permanent_extra(target.id, generation_delta, supabase_url, service_key)
            premium = is_premium_user(target.id, supabase_url, service_key)
            base = 8 if premium else 2
            actual_delta = new - old
            if generation_delta < 0 and old == 0:
                await message.reply_text(f"{display} non ha generazioni extra da rimuovere.")
            else:
                requested = abs(generation_delta)
                if generation_delta < 0 and abs(actual_delta) < requested:
                    detail = f"Rimosse {abs(actual_delta)} generazioni extra (non era possibile scendere sotto 0)."
                else:
                    detail = f"Generazioni extra {'aggiunte' if actual_delta > 0 else 'rimosse'}: {abs(actual_delta)}."
                await message.reply_text(f"{detail}\nUtente: {display}.\nExtra permanenti: {new}.\nNuova quota mensile: {base + new} generazioni.")
        except Exception as exc:
            print("AI QUOTA BONUS ERROR:", repr(exc), flush=True); await message.reply_text("Errore durante l'aggiornamento delle generazioni extra.")
        return True

    if not _is_owner(message):
        await message.reply_text("Questo comando è riservato esclusivamente a Sens."); return True

    if q in {"lista premium", "premium list"}:
        try:
            rows = list_premium_users(supabase_url, service_key)
            lines = ["UTENTI PREMIUM AI"] if rows else ["Nessun utente Premium AI configurato."]
            for row in rows: lines.append(f"- {row.get('note') or 'Utente'} — ID {row['telegram_user_id']} — 8 generazioni/mese + eventuali extra")
            lines.append(f"Posti disponibili: {MAX_PREMIUM_USERS-len(rows)}/{MAX_PREMIUM_USERS}")
            await message.reply_text("\n".join(lines))
        except Exception as exc:
            print("PREMIUM LIST ERROR:", repr(exc), flush=True); await message.reply_text("Errore durante la lettura degli utenti Premium AI.")
        return True

    if q in {"rendimi premium", "rimuovimi premium"}: target = message.from_user
    else:
        target_message = message.reply_to_message; target = target_message.from_user if target_message else None
        if not target or target.is_bot:
            await message.reply_text("Rispondi a un messaggio della persona che vuoi aggiungere o rimuovere e usa questo comando."); return True

    display = target.full_name or target.username or str(target.id)
    try:
        if q in {"rendi premium", "rendimi premium"}:
            result = add_premium_user(target.id, display, supabase_url, service_key)
            if result == "full": await message.reply_text("I 2 posti Premium AI sono già occupati. Rimuovine uno prima di aggiungerne un altro.")
            elif result == "already": await message.reply_text(f"{display} è già un utente Premium AI.")
            else: await message.reply_text(f"{display} è ora un utente Premium AI.\nQuota: 8 generazioni AI al mese + eventuali extra permanenti.")
        else:
            remove_premium_user(target.id, supabase_url, service_key); await message.reply_text(f"Premium AI rimosso da {display}.")
    except Exception as exc:
        print("PREMIUM COMMAND ERROR:", repr(exc), flush=True); await message.reply_text("Errore durante l'aggiornamento del Premium AI. Riprova tra poco.")
    return True
