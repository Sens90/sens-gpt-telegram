import requests

MAX_PREMIUM_USERS = 2
OWNER_TELEGRAM_ID = 437136453


def _headers(service_key):
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }


def list_premium_users(supabase_url, service_key):
    if not supabase_url or not service_key:
        raise RuntimeError("Supabase non configurato")
    r = requests.get(
        f"{supabase_url}/rest/v1/ai_profile_privileged_users",
        headers=_headers(service_key),
        params={"select": "telegram_user_id,note,created_at", "order": "created_at.asc"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def is_premium_user(user_id, supabase_url, service_key):
    if not user_id or not supabase_url or not service_key:
        return False
    r = requests.get(
        f"{supabase_url}/rest/v1/ai_profile_privileged_users",
        headers=_headers(service_key),
        params={"telegram_user_id": f"eq.{int(user_id)}", "select": "telegram_user_id", "limit": "1"},
        timeout=15,
    )
    r.raise_for_status()
    return bool(r.json())


def add_premium_user(user_id, note, supabase_url, service_key):
    user_id = int(user_id)
    current = list_premium_users(supabase_url, service_key)
    if any(int(row["telegram_user_id"]) == user_id for row in current):
        return "already"
    if len(current) >= MAX_PREMIUM_USERS:
        return "full"
    r = requests.post(
        f"{supabase_url}/rest/v1/ai_profile_privileged_users",
        headers={**_headers(service_key), "Prefer": "return=minimal"},
        json={"telegram_user_id": user_id, "note": (note or "Premium AI")[:200]},
        timeout=15,
    )
    r.raise_for_status()
    return "added"


def remove_premium_user(user_id, supabase_url, service_key):
    user_id = int(user_id)
    r = requests.delete(
        f"{supabase_url}/rest/v1/ai_profile_privileged_users",
        headers={**_headers(service_key), "Prefer": "return=minimal"},
        params={"telegram_user_id": f"eq.{user_id}"},
        timeout=15,
    )
    r.raise_for_status()
    return True


def _is_owner(message):
    return bool(message.from_user and int(message.from_user.id) == OWNER_TELEGRAM_ID)


async def handle_premium_command(message, context, question, supabase_url, service_key):
    q = " ".join((question or "").casefold().split())
    commands = {
        "mio id", "id telegram", "telegram id",
        "rendimi premium", "rimuovimi premium",
        "rendi premium", "rimuovi premium", "lista premium", "premium list"
    }
    if q not in commands:
        return False

    # Everyone can read only their own Telegram numeric ID.
    if q in {"mio id", "id telegram", "telegram id"}:
        user = message.from_user
        await message.reply_text(
            f"Il tuo Telegram User ID è: {user.id}\n"
            "Questo è l'ID Telegram numerico, non il tag di Brawl Stars."
        )
        return True

    # All Premium management commands are reserved exclusively for Sens.
    if not _is_owner(message):
        await message.reply_text("Questo comando è riservato esclusivamente a Sens.")
        return True

    if q in {"lista premium", "premium list"}:
        try:
            rows = list_premium_users(supabase_url, service_key)
            if not rows:
                await message.reply_text("Nessun utente Premium AI configurato. Posti disponibili: 2/2.")
            else:
                lines = ["UTENTI PREMIUM AI"]
                for row in rows:
                    lines.append(f"- {row.get('note') or 'Utente'} — ID {row['telegram_user_id']}")
                lines.append(f"Posti disponibili: {MAX_PREMIUM_USERS-len(rows)}/{MAX_PREMIUM_USERS}")
                await message.reply_text("\n".join(lines))
        except Exception as exc:
            print("PREMIUM LIST ERROR:", repr(exc), flush=True)
            await message.reply_text("Errore durante la lettura degli utenti Premium AI.")
        return True

    if q in {"rendimi premium", "rimuovimi premium"}:
        target = message.from_user
    else:
        target_message = message.reply_to_message
        target = target_message.from_user if target_message else None
        if not target or target.is_bot:
            await message.reply_text("Rispondi a un messaggio della persona che vuoi aggiungere o rimuovere e usa questo comando.")
            return True

    display = target.full_name or target.username or str(target.id)
    try:
        if q in {"rendi premium", "rendimi premium"}:
            result = add_premium_user(target.id, display, supabase_url, service_key)
            if result == "full":
                await message.reply_text("I 2 posti Premium AI sono già occupati. Rimuovine uno prima di aggiungerne un altro.")
            elif result == "already":
                await message.reply_text(f"{display} è già un utente Premium AI.")
            else:
                await message.reply_text(
                    f"{display} è ora un utente Premium AI.\n"
                    "Generazioni AI: illimitate rispetto alla quota del bot.\n"
                    "Può generare profili di altri giocatori e usare la generazione AI libera."
                )
        else:
            remove_premium_user(target.id, supabase_url, service_key)
            await message.reply_text(f"Premium AI rimosso da {display}.")
    except Exception as exc:
        print("PREMIUM COMMAND ERROR:", repr(exc), flush=True)
        await message.reply_text("Errore durante l'aggiornamento del Premium AI. Riprova tra poco.")
    return True
