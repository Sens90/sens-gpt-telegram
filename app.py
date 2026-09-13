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
        "brawler", "brawlers", "rotazione", "rotazione attuale", "mappa attuale", "mappe attuali", "mappa corrente", "mappe correnti", "mappa di oggi", "mappe di oggi", "miglior comp", "migliore comp", "composizione", "composizione migliore",
        "modalità", "modalita",
        "gadget", "ingranaggio", "star power",
        "ipercarica", "overdrive",
        "shade", "leon", "mortis"
    ]

    question_lower = question.lower()

    return any(keyword in question_lower for keyword in keywords)


def web_search(query):
    query_lower = query.lower()

    is_map_query = any(x in query_lower for x in [
        "mappa", "mappe", "rotazione", "mappa attuale",
        "mappa di oggi", "mappe attuali", "mappa corrente"
    ])

    is_image_subject_query = any(x in query_lower for x in [
        "brawler", "brawlers", "skin", "skins", "costume"
    ])

    if is_map_query:
        search_query = (
            f"Brawl Stars {query} "
            f"(site:brawlinsights.com/en/tools/map_rotation OR site:brawlify.com/it/maps) "
            f"Brawl Insights current map rotation"
        )
    elif is_image_subject_query:
        search_query = (
            f"Brawl Stars {query} "
            f"site:brawlstars.wiki"
        )
    else:
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
            "include_raw_content": True,
            "include_images": True,
            "exclude_domains": [
                "pinterest.com",
                "youtube.com",
                "youtu.be",
                "ytimg.com",
                "tiktok.com",
                "vimeo.com"
            ]
        },
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    if is_map_query:
        fallback_response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f"Brawl Stars {query} site:brawlify.com/it/maps current live rotation",
                "search_depth": "advanced",
                "max_results": 8,
                "include_answer": False,
                "include_raw_content": True,
                "include_images": False
            },
            timeout=20
        )

        fallback_response.raise_for_status()
        fallback_data = fallback_response.json()

        data.setdefault("results", [])
        data["results"].extend(fallback_data.get("results", []))

    return data


def image_search(query):
    search_query = (
        f"Brawl Stars {query} "
        f"site:liquipedia.net/brawlstars "
        f"map brawler skin image"
    )

    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": search_query,
            "search_depth": "advanced",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": True,
            "include_domains": [
                "liquipedia.net"
            ]
        },
        timeout=20
    )

    response.raise_for_status()
    return response.json()


async def send_relevant_images(context, chat_id, question, images):
    question_lower = question.lower()

    image_keywords = [
        "mappa", "mappe", "mappa attuale", "mappa di oggi",
        "brawler", "brawlers", "skin", "skins", "costume"
    ]

    if not any(keyword in question_lower for keyword in image_keywords):
        return

    sent = 0
    seen = set()

    for image in images:
        if isinstance(image, dict):
            image_url = image.get("url")
            description = (image.get("description") or "").lower()
        else:
            image_url = image
            description = ""
        if any(k in question_lower for k in ["brawler", "brawlers", "skin", "skins", "costume"]):
            ignored_words = {"brawler", "brawlers", "skin", "skins", "costume", "parlami", "dimmi", "qual", "quale", "della", "delle", "del", "dei", "degli", "una", "uno", "con", "per", "che", "come", "migliore", "miglior"}
            search_terms = [word.strip(".,!?():;") for word in question_lower.split() if len(word.strip(".,!?():;")) >= 3 and word.strip(".,!?():;") not in ignored_words]
            image_text = f"{description} {image_url or ''}".lower()
            if search_terms and not any(term in image_text for term in search_terms):
                print("IMMAGINE NON PERTINENTE:", image_url, description, flush=True)
                continue

        if not image_url or image_url in seen:
            continue

        seen.add(image_url)

        try:
            image_response = requests.get(
                image_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )

            image_response.raise_for_status()

            content_type = image_response.headers.get("Content-Type", "").lower()

            if not content_type.startswith("image/"):
                print("IMMAGINE SCARTATA:", image_url, content_type, flush=True)
                continue

            if len(image_response.content) > 10 * 1024 * 1024:
                print("IMMAGINE TROPPO GRANDE:", image_url, flush=True)
                continue

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_response.content
            )

            sent += 1

            if sent >= 1:
                break

        except Exception as e:
            print("ERRORE INVIO IMMAGINE:", repr(e), flush=True)







async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    bot_username = context.bot.username

    if not bot_username:
        return

    mentioned = (
        f"@{bot_username.lower()}" in message.text.lower()
    )

    is_reply = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == context.bot.id
    )

    if not mentioned and not is_reply:
        return

    question = message.text

    if mentioned:
        question = question.replace(
            f"@{bot_username}",
            ""
        ).strip()

    original_message = ""

    if is_reply:
        replied_message = message.reply_to_message

        if replied_message and replied_message.text:
            original_message = replied_message.text.strip()

    if not question:
        if original_message:
            question = (
                "Analizza e rispondi al seguente messaggio:\n"
                f"{original_message}"
            )
        else:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=(
                    "Sono Sens GPT, l'AI ufficiale dei TITANI ABUSIVI. "
                    "Fammi una domanda su Brawl Stars."
                )
            )
            return

    if original_message and question:
        question_for_ai = (
            "L'utente sta rispondendo a questo messaggio:\n"
            f"\"{original_message}\"\n\n"
            "La sua domanda o risposta è:\n"
            f"\"{question}\"\n\n"
            "Usa il messaggio originale come contesto."
        )
    else:
        question_for_ai = question

    web_context = ""
    web_sources = []
    web_images = []

    if needs_web_search(question_for_ai):
        try:
            search_data = web_search(question_for_ai)

            for result in search_data.get("results", []):
                title = result.get("title", "")
                content = result.get("content", "")
                raw_content = result.get("raw_content", "") or ""
                url = result.get("url", "")

                if title or content:
                    web_context += (
                        f"\nTitolo: {title}\n"
                        f"Contenuto: {content}\n"
                        f"Contenuto completo: {raw_content[:6000]}\n"
                        f"Fonte: {url}\n"
                        f"---\n"
                    )

                if url:
                    web_sources.append(url)

            try:
                image_data = image_search(question_for_ai)
                for image in image_data.get("images", []):
                    if isinstance(image, str) and image.startswith("http"):
                        web_images.append({"url": image, "description": ""})
                    elif isinstance(image, dict):
                        image_url = image.get("url") or image.get("image_url")
                        description = image.get("description") or image.get("title") or ""
                        if image_url and image_url.startswith("http"):
                            web_images.append({"url": image_url, "description": description})

            except Exception as e:
                print("ERRORE RICERCA IMMAGINI:", repr(e), flush=True)
            print(
                f"TAVILY: trovati {len(web_sources)} risultati e {len(web_images)} immagini",
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
                "- Considera Reddit, YouTube, wiki e siti specializzati "
                "di Brawl Stars.\n"
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

                "STILE DI RISPOSTA:\n"
                "- Rispondi come l'assistente AI ufficiale della community TITANI ABUSIVI.\n"
                "- Mantieni uno stile diretto, professionale, chiaro e naturale.\n"
                "- Usa un italiano corretto, semplice e non eccessivamente formale.\n"
                "- Vai subito al punto senza introduzioni inutili.\n"
                "- Non usare espressioni ripetitive come \"Bella Titano!\" o \"Ciao Titano!\".\n"
                "- Non usare Markdown decorativo.\n"
                "- Non usare asterischi per grassetto o corsivo.\n"
                "- Non usare #, ## o ### per creare titoli.\n"
                "- Non usare formattazioni elaborate o inutili.\n"
                "- Usa paragrafi brevi e facilmente leggibili.\n"
                "- Usa elenchi con il carattere - solo quando migliorano realmente la leggibilità.\n"
                "- Non ripetere la domanda dell'utente prima di rispondere.\n"
                "- Non aggiungere conclusioni inutili o frasi come \"Spero di esserti stato utile\".\n"
                "- Non inventare informazioni mancanti.\n"
                "- Se non sei sicuro di un dato, dichiaralo chiaramente.\n"
                "- Quando utilizzi fonti web, inserisci le fonti alla fine della risposta in modo semplice e ordinato.\n"
                "- La risposta deve sembrare scritta da un assistente ufficiale della community, non da un chatbot che cerca di essere simpatico.\n\n"
                "FONTE PRIORITARIA PER LE MAPPE:\n"
                "- Per la rotazione delle mappe attuali usa Brawl Insights come fonte primaria.\n"
                "- La fonte primaria per la rotazione è https://brawlinsights.com/en/tools/map_rotation.\n"
                "- Se Brawl Insights non permette di verificare la mappa corrente, usa Brawlify come fonte di fallback live.\n"
                "- Per il fallback live usa https://brawlify.com/it/maps.\n"
                "- Usa Brawlify solo se mostra chiaramente la rotazione corrente; non usare mappe storiche come se fossero attive.\n"
                "- Non usare mappe storiche o risultati provenienti da altre fonti per dichiarare quale mappa è attiva se Brawl Insights fornisce il dato.\n"
                "- Se la rotazione attuale non è verificabile, dichiaralo chiaramente e non indovinare.\n\n"

                "- Non inventare traduzioni di nomi ufficiali.\n"
                "- Per una miglior composizione identifica prima la mappa corrente e poi scegli i Brawler più adatti a quella specifica mappa.\n\n"

                "Alla fine della risposta aggiungi:\n"
                "Fonti:\n"
                "e indica le fonti web realmente utilizzate.\n\n"

                f"RISULTATI DELLA RICERCA WEB:\n"
                f"{web_context}\n\n"

                f"DOMANDA E CONTESTO:\n"
                f"{question_for_ai}"
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

                f"DOMANDA E CONTESTO:\n"
                f"{question_for_ai}"
            )

        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=instructions
        )

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=response.text
        )

        if any(k in question_for_ai.lower() for k in ["mappa", "mappe", "rotazione"]):
            try:
                map_image_data = image_search(f"{question_for_ai} {response.text}")
                web_images = []
                for image in map_image_data.get("images", []):
                    if isinstance(image, str) and image.startswith("http"):
                        web_images.append({"url": image, "description": ""})
                    elif isinstance(image, dict):
                        image_url = image.get("url") or image.get("image_url")
                        description = image.get("description") or image.get("title") or ""
                        if image_url and image_url.startswith("http"):
                            web_images.append({"url": image_url, "description": description})
            except Exception as e:
                print("ERRORE IMMAGINE MAPPA:", repr(e), flush=True)

        await send_relevant_images(
            context,
            message.chat_id,
            question_for_ai,
            web_images
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
