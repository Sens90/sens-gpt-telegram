from pathlib import Path

p = Path("app.py")
s = p.read_text(encoding="utf-8")

imp = "from ai_profile_generator import generate_scene, overlay_stats, quota_status, consume_quota\n"
anchor = "from ai_profile_experience import build_visual_prompt, choose_scene\n"
if imp not in s:
    if anchor not in s:
        raise SystemExit("AI import anchor not found")
    s = s.replace(anchor, anchor + imp, 1)

start_marker = "    profile_image_match = re.fullmatch("
end_marker = "\n    stats_match = re.fullmatch("
start = s.index(start_marker)
end = s.index(end_marker, start)

block = '''    profile_image_match = re.fullmatch(
        r"(?:profilo ai|profilo grafico|immagine profilo|profile image)(?:\\s+(sorprendimi|brawl|cinema|epico|fantascienza|fantasy))?\\s*#?([0289PYLQGRJCUV]{3,15})(?:\\s+con\\s+([A-Za-z0-9À-ÿ ._'’-]{2,30}))?",
        question.strip(), re.I
    )
    if profile_image_match:
        category_map = {
            "sorprendimi": "random", "brawl": "official", "cinema": "cinema",
            "epico": "epic", "fantascienza": "scifi", "fantasy": "fantasy"
        }
        category = category_map.get((profile_image_match.group(1) or "sorprendimi").lower(), "random")
        requested_brawler = (profile_image_match.group(3) or "").strip() or None
        telegram_user_id = message.from_user.id

        try:
            quota = await asyncio.to_thread(quota_status, telegram_user_id)
        except Exception as error:
            print("AI PROFILE QUOTA CHECK:", repr(error), flush=True)
            await message.reply_text("Il controllo della quota AI non è disponibile. Riprova tra poco.")
            return

        if not quota["unlimited"] and quota["used"] >= quota["limit"]:
            await message.reply_text(
                f"Hai già usato le {quota['limit']} generazioni Profilo AI disponibili questo mese. "
                "Puoi chiedere al Presidente Sens o ad Anna di generarlo per te."
            )
            return

        tag = profile_image_match.group(2).upper()
        player = await asyncio.to_thread(get_brawltrack_player, tag)
        if not player:
            player = await asyncio.to_thread(get_brawlzone_player, tag)
        if not player:
            await message.reply_text("Non riesco a trovare questo giocatore.")
            return

        if str(player.get("tag") or "").upper().replace("#", "") == "2VQYLG0RU8":
            player["club"] = player["club_name"] = "TALENTI ABUSIVI"

        member_data = community.get_member_by_player_tag(message.chat_id, player.get("tag")) or {}
        for key in (
            "ranked_current", "ranked_season_peak", "ranked_career_peak", "ranked_peak",
            "ranked_current_elo", "ranked_season_peak_elo", "ranked_career_peak_elo", "prestige"
        ):
            if not player.get(key) and member_data.get(key):
                player[key] = member_data[key]

        # Optional override requested by the member. The generator must still
        # receive a real visual reference before producing the final image.
        if requested_brawler:
            player["profile_brawler"] = requested_brawler
            player["requested_brawler"] = requested_brawler

        await context.bot.send_chat_action(chat_id=message.chat_id, action="upload_photo")
        progress = await message.reply_text("Sto creando il tuo Profilo AI TITANI ABUSIVI…")
        try:
            scene_bytes, scene = await asyncio.to_thread(generate_scene, player, category)
            card = await asyncio.to_thread(overlay_stats, scene_bytes, player)
            await context.bot.send_photo(
                chat_id=message.chat_id,
                photo=card,
                caption=(
                    f"Profilo AI TITANI ABUSIVI • {player.get('name','Giocatore')}\\n"
                    f"Scena: {scene.get('place','cinematografica')}"
                )
            )
            quota = await asyncio.to_thread(consume_quota, telegram_user_id)
            quota_text = (
                "Profilo AI • quota illimitata" if quota["unlimited"]
                else f"Profilo AI generato • Questo mese {quota['used']}/{quota['limit']}"
            )
            await progress.edit_text(quota_text)
        except Exception as error:
            print("AI PROFILE GENERATION:", repr(error), flush=True)
            await progress.edit_text("La generazione AI non è riuscita. La quota non è stata consumata.")
        return
'''

s = s[:start] + block + s[end:]
p.write_text(s, encoding="utf-8")
print("AI profile integration applied directly to app.py")
