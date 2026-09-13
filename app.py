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
        "oggi", "ieri", "domani", "attuale", "attualmente",
        "adesso", "ora", "ultimo", "ultimi", "ultima",
        "nuovo", "nuova", "novità", "novita",
        "aggiornamento", "aggiornamenti", "patch",
        "buff", "nerf", "bilanciamento", "meta",
        "stagione", "evento", "eventi",
        "classifica", "classifiche",
        "quando esce", "uscito", "uscita", "rilascio",
        "prezzo", "quanto costa",
        "brawler", "brawlers",
        "modalità", "modalita",
        "gadget", "ingranaggio", "star power",
        "ipercarica", "overdrive",
        "shade", "leon", "mortis"
    ]

    question_lower = question.lower()

    return any(keyword in question_lower for keyword in keywords)


def web_search(query):
    search_query = (
        f"Brawl Stars {query} "
        f"brawler aggiornamenti notizie informazioni"
    )

    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": search_query,
            "search_depth": "advanced",
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "exclude_domains": [
                "pinterest.com"
            ]
        },
        timeout=20
    )

    response.raise_for_status()

    return response.json()


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    bot_username = context.bot.username

    if not bot_username:
        return

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
    web_sources = []

    if needs_web_search(question):
        try:
            search_data = web_search(question)

            for result in search_data.get("results", []):
                title = result.get("title", "")
                content = result.get("content", "")
                url = result.get("url", "")

                if title or content:
                    web_context += (
                        f"\nTitolo: {title}\n"
                        f"Contenuto: {content}\n"
                        f"Fonte: {url}\n"
                        f"---\n"
                    )

                if url:
                    web_sources.append(url)

            print(
                f"TAVILY: trovati {len(web_sources)} risultati",
                flush=True
            )

        except Exception as e:
            print(
                "ERRORE TAVILY:",
                repr(e),
                flush=True
            )

    try:
        if web_context:
            instructions = (
                "Sei Sens GPT, l'intelligenza artificiale ufficiale "
                "della community TITANI ABUSIVI.\n\n"

                "Sei specializzato in Brawl Stars e devi conoscere "
                "il gioco in modo approfondito.\n\n"

                "Sono state effettuate ricerche web specifiche per "
                "rispondere alla domanda dell'utente.\n\n"

                "USA LE INFORMAZIONI WEB FORNITE.\n\n"

                "Regole fondamentali:\n"
                "- Non inventare informazioni.\n"
                "- Non affermare che un Brawler, modalità, evento o "
                "funzione non esiste solamente perché non compare "
                "in una fonte ufficiale.\n"
                "- Considera anche Reddit, YouTube, wiki e siti "
                "specializzati di Brawl Stars.\n"
                "- Dai priorità alle fonti ufficiali quando si parla "
                "di informazioni ufficialmente annunciate.\n"
                "- Usa fonti della community per informazioni storiche, "
                "guide, statistiche e informazioni consolidate.\n"
                "- Distingui chiaramente informazioni ufficiali, "
                "informazioni della community e rumor/leak.\n"
                "- Se trovi fonti discordanti, spiegalo.\n"
                "- Se una informazione è vecchia, considera la data "
                "della fonte prima di rispondere.\n"
                "- Per domande su un Brawler specifico, cerca e usa "
                "le informazioni relative a quel Brawler anche se "
                "non sono presenti sul sito ufficiale di Supercell.\n"
                "- Non dire che non esiste un Brawler solo perché "
                "non lo trovi nelle fonti ufficiali.\n\n"

                "Alla fine della risposta aggiungi:\n"
                "Fonti:\n"
                "e indica le fonti web realmente utilizzate.\n\n"

                f"RISULTATI DELLA RICERCA WEB:\n"
                f"{web_context}\n\n"

                f"DOMANDA DELL'UTENTE:\n"
                f"{question}"
            )

        else:
            instructions = (
                "Sei Sens GPT, l'intelligenza artificiale ufficiale "
                "della community TITANI ABUSIVI.\n\n"

                "Sei specializzato in Brawl Stars. "
                "Rispondi sempre in italiano, in modo competente, "
                "diretto, chiaro e utile.\n\n"

                "Non inventare informazioni. "
                "Se non conosci con certezza una informazione, "
                "dillo chiaramente.\n\n"

                f"DOMANDA DELL'UTENTE:\n{question}"
            )

        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=instructions
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
