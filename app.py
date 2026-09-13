import os
from flask import Flask
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)
import threading

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

@app.route("/")
def home():
    return "Sens GPT - TITANI ABUSIVI ONLINE"


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    bot_username = context.bot.username

    if f"@{bot_username.lower()}" not in message.text.lower():
        return

    question = message.text.replace(
        f"@{bot_username}", ""
    ).strip()

    if not question:
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=(
                "Sono Sens GPT, l'AI ufficiale dei TITANI ABUSIVI. "
                "Fammi una domanda su Brawl Stars."
            )
        )
        return

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=(
                "Sei Sens GPT, l'intelligenza artificiale ufficiale "
                "della community TITANI ABUSIVI. "
                "Sei specializzato soprattutto in Brawl Stars. "
                "Rispondi sempre in italiano, in modo competente, "
                "diretto, chiaro e utile. "
                "Non inventare informazioni. "
                "Quando la domanda riguarda informazioni attuali, "
                "aggiornamenti, bilanciamenti, nuovi Brawler, modalità, "
                "meta, eventi o qualsiasi informazione che potrebbe essere "
                "cambiata recentemente, usa la ricerca Google per verificare "
                "le informazioni prima di rispondere.\n\n"
                f"Domanda dell'utente: {question}"
            ),
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        google_search=types.GoogleSearch()
                    )
                ]
            )
        )

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=response.text
        )

    except Exception as e:
        print("ERRORE GEMINI:", repr(e), flush=True)
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=(
                "Ho avuto un problema con il sistema AI. "
                "Riprova tra poco."
            )
        )


def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer
        )
    )

    application.run_polling()


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    main()                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          