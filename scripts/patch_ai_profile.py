from pathlib import Path

p = Path("app.py")
s = p.read_text(encoding="utf-8")

old = '''            card = await asyncio.to_thread(overlay_stats, scene_bytes, player)
            await context.bot.send_photo(
                chat_id=message.chat_id,
                photo=card,
'''
new = '''            card, card_filename = await asyncio.to_thread(overlay_stats, scene_bytes, player)
            # Pass the file object itself to Telegram. Passing the complete
            # (BytesIO, filename) tuple makes the HTTP layer try to serialize
            # BytesIO as JSON and the upload fails.
            card.name = card_filename
            card.seek(0)
            await context.bot.send_photo(
                chat_id=message.chat_id,
                photo=card,
'''

if new in s:
    print("AI profile Telegram upload fix already applied")
elif old in s:
    s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")
    print("AI profile Telegram upload fixed")
else:
    raise SystemExit("AI profile upload anchor not found; app.py left unchanged")
