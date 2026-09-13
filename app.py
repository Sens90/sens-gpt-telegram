import os
import requests
import threading

from flask import Flask
from google import genai
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]

client = genai.Client(api_key=GEMINI_API_KEY)

app = Flask(__name__)


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


@app.route("/")
def home():
    return "Sens GPT - TITANI ABUSIVI ONLINE"


def needs_web_search(question):
    keywords = [
        "oggi",
        "attuale",
        "attualmente",
        "ultimo",
        "ultimi",
        "nuovo",
        "nuova",
        "novità",
        "aggiornamento",
        "aggiornamenti",
        "patch",
        "buff",
        "nerf",
        "bilanciamento",
        "meta",
        "stagione",
        "evento",
        "eventi",
        "classifica",
        "classifiche",
        "quando esce",
        "uscito",
        "uscita",
        "prezzo",
        "quanto costa"
    ]

    question_lower = question.lower()

    return any(keyword in question_lower for keyword in keywords)


def web_search(query):
    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": 5
        },
        timeout=15
    )

    response.raise_for_status()

    return response.json()


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    bot_username = context.bot.username

    if f"@{bot_username.lower()}" not in message.text.lower():
        return

    question = message.text.replace(
        f"@{bot_username}",
        ""
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

    web_context = ""

    if needs_web_search(question):
        try:
            search_data = web_search(question)

            for result in search_data.get("results", []):
                title = result.get("title", "")
                content = result.get("content", "")
                url = result.get("url", "")

                web_context += (
                    f"\nTitolo: {title}\n"
                    f"Contenuto: {content}\n"
                    f"Fonte: {url}\n"
                )

        except Exception as e:
            print(
                "ERRORE TAVILY:",
                repr(e),
                flush=True
            )

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=(
                "Sei Sens GPT, l'intelligenza artificiale ufficiale "
                "della community TITANI ABUSIVI. "
                "Sei specializzato soprattutto in Brawl Stars. "
                "Rispondi sempre in italiano, in modo competente, "
                "diretto, chiaro e utile. "
                "Non inventare informazioni.\n\n"

                "Se sono presenti informazioni provenienti dal web, "
                "usale per rispondere alle domande che riguardano "
                "informazioni attuali. "
                "Considera le informazioni web come fonti da verificare "
                "e non inventare dati che non sono presenti.\n\n"

                f"Informazioni aggiornate dal web:\n"
                f"{web_context}\n\n"

                f"Domanda dell'utente: {question}"
            )
        )

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=response.text
        )

    except Exception as e:
        print(
            "ERRORE GEMINI:",
            repr(e),
            flush=True
        )

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=(
                "Ho avuto un problema con il sistema AI. "
                "Riprova tra poco."
            )
        )


def main():
    application = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer
        )
    )

    application.run_polling()


if __name__ == "__main__":
    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    main()                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     