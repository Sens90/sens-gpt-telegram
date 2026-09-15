import io
import html
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import os
import requests
import re
import threading

from flask import Flask
from google import genai
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from community_features import CommunityFeatures


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

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
        "buff", "nerf", "bilanciamento", "meta", "push", "pushare", "pushare adesso", "cosa giocare", "giocare adesso", "cosa devo giocare",
        "tier list", "tierlist", "miglior brawler", "migliori brawler",
        "ranked", "competitivo", "pick rate", "win rate",
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


def is_current_meta_query(question):
    question_lower = question.lower()

    meta_keywords = [
        "meta", "tier list", "tierlist", "push", "pushare", "cosa pushare", "cosa giocare", "giocare adesso", "cosa devo giocare",
        "miglior brawler", "migliori brawler",
        "ranked", "competitivo",
        "pick rate", "win rate",
        "buff", "nerf", "bilanciamento"
    ]

    return any(keyword in question_lower for keyword in meta_keywords)


def get_game_context(question):
    q = (question or "").lower()

    ranked_terms = [
        "classificata", "classificate", "ranked", "draft", "ban",
        "lega", "leghe", "power league"
    ]
    ladder_terms = [
        "ladder", "trofei", "trofeo", "coppe", "coppa",
        "push", "pushare", "scalare", "scala trofei"
    ]

    if any(term in q for term in ranked_terms):
        return "ranked"

    if any(term in q for term in ladder_terms):
        return "ladder"

    return "both"


def meta_source_priority(url):
    url_lower = (url or "").lower()

    if "supercell.com" in url_lower or "brawlstars.com" in url_lower:
        return 0
    if "brawlplanet.com" in url_lower or "brawlplanet.nl" in url_lower:
        return 1
    if "brawlify.com" in url_lower or "brawltime.ninja" in url_lower:
        return 2
    if "noff.gg" in url_lower:
        return 3
    return 4


def web_search(query):
    query_lower = query.lower()
    today = datetime.now(timezone.utc).date().isoformat()
    is_meta_query = is_current_meta_query(query)
    game_context = get_game_context(query)

    if game_context == "ranked":
        context_hint = "Ranked Classificata current ranked map pool draft ban"
    elif game_context == "ladder":
        context_hint = "trophy ladder trofei current event rotation"
    else:
        context_hint = "compare trophy ladder and Ranked separately"

    is_map_query = any(x in query_lower for x in [
        "mappa", "mappe", "rotazione", "mappa attuale",
        "mappa di oggi", "mappe attuali", "mappa corrente"
    ])

    is_image_subject_query = any(x in query_lower for x in [
        "brawler", "brawlers", "skin", "skins", "costume"
    ])

    if is_map_query:
        search_query = (
            f"Brawl Stars {query} {today} {context_hint} "
            f"Brawl Planet italiano mappe attive win rate pick rate giocatore stella team comp "
            f"site:brawlplanet.nl/it OR site:brawlplanet.com/it "
            f"Brawl Insights Brawlify current live rotation current season"
        )
    elif is_meta_query:
        search_query = (
            f"Brawl Stars current meta {today} {query} {context_hint} "
            f"Brawl Planet italiano win rate pick rate giocatore stella team comp "
            f"latest balance changes tier list competitive "
            f"gadget abilità stellare equipaggiamento overdrive nomi italiani "
            f"Supercell italiano Brawl Planet Brawlify Brawl Time Ninja Noff"
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
            "include_answer": True,
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


    return data



def extract_brawl_ball_map(search_data):
    results = search_data.get("results", [])

    for domain in [
        "brawlinsights.com",
        "brawlify.com"
    ]:
        for result in results:
            url = (result.get("url") or "").lower()

            if domain not in url:
                continue

            text = (
                (result.get("title") or "")
                + "\n"
                + (result.get("content") or "")
                + "\n"
                + (result.get("raw_content") or "")
            )

            print(
                "DATI ROTAZIONE DA",
                domain,
                ":",
                text[text.find("| Date and Time (UTC) | Mode | Map |"):][:12000] if "| Date and Time (UTC) | Mode | Map |" in text else text[-12000:],
                flush=True
            )

    print(
        "MAPPA BRAWL BALL LIVE NON ANCORA ESTRATTA",
        flush=True
    )

    return None


def get_verified_map_comp(map_name):
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f"\"{map_name}\" \"Best Teams\" Brawl Ball",
                "search_depth": "advanced",
                "max_results": 5,
                "include_answer": False,
                "include_raw_content": True,
                "include_images": False,
                "include_domains": [
                    "brawltime.ninja",
                    "powerleagueprodigy.com"
                ]
            },
            timeout=20
        )

        response.raise_for_status()
        data = response.json()

        for result in data.get("results", []):
            url = (result.get("url") or "").lower()
            text = (
                (result.get("raw_content") or "")
                + "\n"
                + (result.get("content") or "")
            )

            if "brawltime.ninja" in url:
                team_match = re.search(
                    r"Best Teams.*?\n\s*1\s*\|\s*([^\n|]+(?:,\s*[^\n|]+){2})",
                    text,
                    re.I | re.S
                )

                if not team_match:
                    team_match = re.search(
                        r"1\s*\|\s*([^\n|]+(?:,\s*[^\n|]+){2})\s*\|\s*\d+",
                        text,
                        re.I
                    )

                if team_match:
                    team = [
                        x.strip()
                        for x in team_match.group(1).split(",")
                        if x.strip()
                    ]

                    if len(team) >= 3:
                        verified = team[:3]

                        print(
                            "COMP VERIFICATA BRAWLTIME:",
                            map_name,
                            verified,
                            flush=True
                        )

                        return verified

        for result in data.get("results", []):
            url = (result.get("url") or "").lower()

            if "powerleagueprodigy.com" not in url:
                continue

            text = (
                (result.get("raw_content") or "")
                + "\n"
                + (result.get("content") or "")
            )

            section_match = re.search(
                r"##\s*" + re.escape(map_name) +
                r"\s*(.*?)(?=\n##\s|\Z)",
                text,
                re.I | re.S
            )

            if not section_match:
                continue

            section = section_match.group(1)

            if (
                "brawl ball" not in section.lower()
                or "top brawlers" not in section.lower()
            ):
                continue

            top_section = section.split("Top Brawlers", 1)[1]

            names = re.findall(
                r"Image:\s*([^\n]+)\s*\n+\s*\1\s*\n",
                top_section,
                re.I
            )

            clean_names = []

            for name in names:
                name = name.strip()

                if name and name.lower() not in [
                    "brawl ball",
                    map_name.lower()
                ]:
                    if name not in clean_names:
                        clean_names.append(name)

            if len(clean_names) >= 3:
                verified = clean_names[:3]

                print(
                    "COMP VERIFICATA FALLBACK:",
                    map_name,
                    verified,
                    flush=True
                )

                return verified

        print(
            "COMP VERIFICATA NON TROVATA:",
            map_name,
            flush=True
        )

    except Exception as e:
        print(
            "ERRORE COMP VERIFICATA:",
            repr(e),
            flush=True
        )

    return []

def search_map_comp(map_name):
    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": f"\"{map_name}\" Brawl Stars Brawl Ball",
            "search_depth": "advanced",
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": True,
            "include_images": False,
            "include_domains": [
                "noff.gg"
            ]
        },
        timeout=20
    )
    response.raise_for_status()

    data = response.json()

    specific_results = []

    map_key = re.sub(r"[^a-z0-9]+", "-", map_name.lower()).strip("-")

    for result in data.get("results", []):
        url = (result.get("url") or "").lower()
        title = (result.get("title") or "").lower()
        content = (
            (result.get("content") or "")
            + "\n"
            + (result.get("raw_content") or "")
        ).lower()

        if (
            f"/brawl-stars/map/{map_key}" in url
            or (
                map_name.lower() in title
                and "brawl ball" in content
            )
        ):
            specific_results.append(result)

    if specific_results:
        print(
            "NOFF MAPPA SPECIFICA TROVATA:",
            map_name,
            flush=True
        )
        return {
            "results": specific_results
        }

    print(
        "NOFF MAPPA SPECIFICA NON TROVATA, USO FALLBACK:",
        map_name,
        flush=True
    )

    fallback_response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": f"\"{map_name}\" \"Brawl Ball\" Brawl Stars best brawlers win rate",
            "search_depth": "advanced",
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": True,
            "include_images": False
        },
        timeout=20
    )

    fallback_response.raise_for_status()
    return fallback_response.json()


def get_noff_map_image(map_name):
    try:
        response = requests.get(
            "https://api.brawlapi.com/v1/maps",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )
        response.raise_for_status()

        data = response.json()

        target = re.sub(
            r"[^a-z0-9]+",
            "",
            map_name.lower()
        )

        for map_data in data.get("list", []):
            name = map_data.get("name") or ""

            name_key = re.sub(
                r"[^a-z0-9]+",
                "",
                name.lower()
            )

            if name_key != target:
                continue

            image_url = map_data.get("imageUrl")

            if image_url:
                print(
                    "IMMAGINE MAPPA TROVATA:",
                    name,
                    image_url,
                    flush=True
                )
                return image_url

        print(
            "IMMAGINE MAPPA NON TROVATA:",
            map_name,
            flush=True
        )

    except Exception as e:
        print(
            "ERRORE IMMAGINE MAPPA:",
            repr(e),
            flush=True
        )

    return None


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




BRAWLER_NAMES_IT = {
    "Amber": "Ambra",
    "Rico": "Stecca",
    "Crow": "Corvo",
    "Gene": "Eugenio",
    "Barley": "Bombardino",
    "Poco": "Pocho",
    "Darryl": "Barryl",
    "Sprout": "Semino",
    "Surge": "Energetik",
    "Gale": "Gelindo",
    "Max": "Maxine",
    "Nani": "Iris",
    "Ruffs": "Ringhio",
    "Colonel Ruffs": "Ringhio"
}


def brawler_name_it(name):
    return BRAWLER_NAMES_IT.get(name, name)


MAP_NAMES_IT = {
    # Ricercati / Bounty
    "No Excuses": "Senza scuse",
    "Shooting Star": "Piana delle stelle",
    "Snake Prairie": "Prateria dei serpenti",
    "Layer Cake": "Arena stratificata",
    "Hideout": "Nascondiglio",

    # Footbrawl / Brawl Ball
    "Second Try": "Secondo tentativo",
    "Trickey": "Red Brawl Arena",
    "Pinhole Punt": "Camp Brawl",
    "Center Stage": "Stamford Brawl",
    "Sneaky Fields": "Campetto incolto",
    "Pinball Dreams": "Brawlacanã",
    "Triple Dribble": "Brawl Trafford",
    "Beach Ball": "Campo da beach brawl",
    "Backyard Bowl": "Campetto",
    "Sunny Soccer": "Campetto sabbioso",
    "Super Beach": "Superspiaggia",
    "Penalty Kick": "Stadio delle Brawlpi",
    "Spiraling Out": "Spirale della vittoria",

    # Duelli
    "Petticoat Duel": "Duello meschino",
    "No Surrender": "Arena dei coraggiosi",
    "Shrouding Serpent": "Serpente sibilante",
    "Warrior's Way": "Via del guerriero",
    "Warrior’s Way": "Via del guerriero",
    "Monkey Maze": "Labirinto delle scimmie",
    "Zen Garden": "Giardino zen",

    # Arraffagemme / Gem Grab
    "Forest Clearing": "Radura nella foresta",
    "The cooler Hard Rock": "Miniera delle acca",
    "Crystal Arcade": "Sala giochi di cristallo",
    "Deathcap Trap": "Antro velenoso",
    "Last Stop": "Ultima fermata",
    "Hard Rock Mine": "Miniera Rocciadura",
    "Double Swoosh": "Arco doppio",
    "Gem Fort": "Fortino delle gemme",
    "Rustic Arcade": "Sala giochi rustica",
    "Open Space": "Campo aperto",
    "Undermine": "Miniera minacciosa",
    "Minecart Madness": "Miniera preziosa",
    "Dungeon Train": "Treno sotterraneo",

    # Rapina / Heist
    "Safe(r) Zone": "Santuario dei santuari",
    "The Great Lake": "Gran lago",
    "GG 2.0": "Colpo grosso",
    "G.G. Mortuary": "Obitorio G.G.",
    "Kaboom Canyon": "Canyon Bum Bum",
    "Safe Zone": "Santuario",
    "Hot Potato": "Battigia ustionante",

    # Dominio / Hot Zone
    "Watersport": "Piscine del dolore",
    "Noisy Neighbors": "Vicini chiassosi",
    "Open Business": "Campo aperto",
    "Dueling Beetles": "Distesa degli scarafaggi",
    "Ring of Fire": "Ring di fuoco",
    "Parallel Plays": "Giocate parallele",
    "In the Liminal": "Spazio liminale",
    "Quick Travel": "Scorrimento veloce",
    "Tread Carefully": "Cautela estrema",

    # K.O. / Knockout
    "H for…": "Acca boschiva",
    "H for...": "Acca boschiva",
    "Two Rivers": "Doppio fiume",
    "Deep Forest": "Foresta fitta",
    "Tiny Islands": "Isolette",
    "Temple of Vroom": "Tempio del tuono",
    "Overgrown Ruins": "Rovine infestate",
    "Goldarm Gulch": "Burrone di Bracciodoro",
    "Out in the Open": "Alla luce del sole",
    "Belle's Rock": "Rupe di Belle",
    "Belle’s Rock": "Rupe di Belle",
    "New Horizons": "Nuovi orizzonti",
    "Flaring Phoenix": "Fenice sfolgorante",
    "Four Levels": "Quattro livelli",
    "Stroke of Luck": "Colpo di fortuna",

    # Annientamento / Wipeout
    "Layer Bake": "Strati verdi",
    "Quad Damage": "Danno quadruplo",
    "The Great Open": "Apertura massima",
    "Infinite Doom": "Rovina infinita",
    "Spice Production": "Arena delle spezie",
    "Slayer's Paradise": "Sogno degli sterminatori",
    "Slayer’s Paradise": "Sogno degli sterminatori",

    # Sopravvivenza / Showdown
    "Acid Lakes": "Laghi acidi",
    "Island Invasion": "Isola invasa",
    "Skull Creek": "Torrente del teschio",
    "Dark Passage": "Passaggio spettrale",
    "Flying Fantasies": "Pianura volante",
    "Rockwall Brawl": "Valle rocciosa",
    "Safety Center": "Rifugio d'emergenza",
    "Feast or Famine": "Piana della fame",
    "Cavern Churn": "Caverna rumorosa",
    "Double Trouble": "Landa dei pericoli",
    "Dried Up River": "Fiume prosciugato",
    "Marksman's Paradise": "Paradiso dei cecchini",
    "Marksman’s Paradise": "Paradiso dei cecchini",

    # Mappe ufficiali introdotte/attive nel 2026
    "In Demand": "Centrocampo affollato",
    "Pump It Up": "Trance agonistica",
    "Stone Skipping": "Rimbalzello",
    "False Sense Of Security": "Mischia ferroviaria",
    "Alchemy": "Alchimia",
    "Net Presence": "Presenza in campo",
    "Triple Threat": "Tripla minaccia",
    "Brawler's Rift": "Faglia dei brawler",
    "Brawler’s Rift": "Faglia dei brawler"
}


def map_name_it(name):
    if not name:
        return name

    lowered = name.casefold()
    for english_name, italian_name in MAP_NAMES_IT.items():
        if english_name.casefold() == lowered:
            return italian_name

    return name


def translate_map_names_in_text(text):
    if not text:
        return text

    translated = text

    for english_name in sorted(MAP_NAMES_IT, key=len, reverse=True):
        italian_name = MAP_NAMES_IT[english_name]
        translated = re.sub(
            r"(?<![A-Za-z0-9])" + re.escape(english_name) + r"(?![A-Za-z0-9])",
            italian_name,
            translated,
            flags=re.I
        )

    return translated


def translate_game_terms_in_text(text):
    if not text:
        return text

    replacements = [
        (r"\bStar\s*Power\b", "abilità stellare"),
        (r"\bStar\s*Powers\b", "abilità stellari"),
        (r"\bGear\b", "equipaggiamento"),
        (r"\bGears\b", "equipaggiamenti"),
        (r"\bHypercharge\b", "overdrive"),
        (r"\bHypercharges\b", "overdrive"),
        (r"\bBuild\b", "configurazione"),
        (r"\bLoadout\b", "configurazione"),
    ]

    translated = text
    for pattern, replacement in replacements:
        translated = re.sub(
            pattern,
            replacement,
            translated,
            flags=re.I
        )

    return translated


def resolve_map_source_name(name):
    if not name:
        return None

    cleaned = name.strip()
    lowered = cleaned.casefold()

    # Nome inglese/canonico già utilizzabile dalla API mappe.
    for english_name in MAP_NAMES_IT:
        if english_name.casefold() == lowered:
            return english_name

    # Se il modello restituisce il nome italiano, risali al nome sorgente.
    for english_name, italian_name in MAP_NAMES_IT.items():
        if italian_name.casefold() == lowered:
            return english_name

    # Consenti comunque nomi non presenti nel dizionario: get_noff_map_image
    # farà la verifica contro l'elenco reale delle mappe di BrawlAPI.
    return cleaned

def get_noff_brawler_image(brawler_name):
    try:
        slug = re.sub(r"[^a-z0-9]+", "_", brawler_name.lower()).strip("_")
        image_url = f"https://www.noff.gg/brawl-stars/res/img/brawlers/{slug}.webp"

        response = requests.get(
            image_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )

        if response.status_code == 200 and response.headers.get("Content-Type", "").lower().startswith("image/"):
            print("IMMAGINE BRAWLER TROVATA:", brawler_name, image_url, flush=True)
            return image_url

        print("IMMAGINE BRAWLER NON TROVATA:", brawler_name, response.status_code, flush=True)

    except Exception as e:
        print("ERRORE IMMAGINE BRAWLER:", brawler_name, repr(e), flush=True)

    return None


async def send_comp_brawler_images(context, chat_id, brawler_names):
    for brawler_name in brawler_names:
        try:
            image_url = get_noff_brawler_image(brawler_name)

            if not image_url:
                continue

            image_response = requests.get(
                image_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15
            )
            image_response.raise_for_status()

            content_type = image_response.headers.get(
                "Content-Type",
                ""
            ).lower()

            if not content_type.startswith("image/"):
                continue

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_response.content,
                caption=brawler_name_it(brawler_name)
            )

        except Exception as e:
            print(
                "ERRORE INVIO IMMAGINE BRAWLER:",
                brawler_name,
                repr(e),
                flush=True
            )



def save_trophy_snapshot(player_tag, player_name, trophies):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("SUPABASE NON CONFIGURATO", flush=True)
        return False

    try:
        tag = player_tag.upper().replace("#", "").strip()

        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/trophy_history",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal"
            },
            json={
                "player_tag": tag,
                "player_name": player_name,
                "trophies": int(trophies)
            },
            timeout=15
        )

        response.raise_for_status()
        return True

    except Exception as e:
        print("ERRORE SALVATAGGIO TROFEI:", repr(e), flush=True)
        return False


def get_trophy_history(player_tag, days=90):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return []

    try:
        tag = player_tag.upper().replace("#", "").strip()
        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/trophy_history",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
            },
            params={
                "player_tag": f"eq.{tag}",
                "recorded_at": f"gte.{since}",
                "select": "trophies,recorded_at",
                "order": "recorded_at.asc"
            },
            timeout=15
        )

        response.raise_for_status()
        return response.json()

    except Exception as e:
        print("ERRORE LETTURA STORICO:", repr(e), flush=True)
        return []


def trophy_value_at_or_before(history, target_time):
    selected = None

    for row in history:
        try:
            dt = datetime.fromisoformat(
                row["recorded_at"].replace("Z", "+00:00")
            )

            if dt <= target_time:
                selected = int(row["trophies"])
            else:
                break

        except Exception:
            continue

    return selected


def calculate_trophy_changes(history, current_trophies):
    now = datetime.now(timezone.utc)

    start_today = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    targets = {
        "today": start_today,
        "7d": now - timedelta(days=7),
        "15d": now - timedelta(days=15),
        "30d": now - timedelta(days=30),
        "90d": now - timedelta(days=90)
    }

    changes = {}

    for key, target in targets.items():
        old_value = trophy_value_at_or_before(
            history,
            target
        )

        changes[key] = (
            current_trophies - old_value
            if old_value is not None
            else None
        )

    return changes


def format_trophy_change(value):
    if value is None:
        return "Storico non disponibile"

    if value > 0:
        return f"+{value:,}".replace(",", ".")

    return f"{value:,}".replace(",", ".")


def get_tracked_player_tags():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return []

    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/trophy_history",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"
            },
            params={
                "select": "player_tag",
                "order": "recorded_at.desc",
                "limit": "5000"
            },
            timeout=20
        )

        response.raise_for_status()

        tags = []

        for row in response.json():
            tag = str(row.get("player_tag", "")).strip().upper()

            if tag and tag not in tags:
                tags.append(tag)

        return tags

    except Exception as e:
        print("ERRORE LETTURA TAG MONITORATI:", repr(e), flush=True)
        return []


def automatic_trophy_monitor():
    import time

    time.sleep(60)

    while True:
        try:
            tags = get_tracked_player_tags()

            print(
                f"MONITOR TROFEI: {len(tags)} giocatori",
                flush=True
            )

            for tag in tags:
                try:
                    player = get_brawlzone_player(tag)

                    if player:
                        save_trophy_snapshot(
                            player["tag"],
                            player["name"],
                            player["trophies"]
                        )

                        print(
                            f"TROFEI AGGIORNATI: {player["name"]} "
                            f"{player["trophies"]}",
                            flush=True
                        )

                    time.sleep(5)

                except Exception as e:
                    print(
                        f"ERRORE MONITOR TAG {tag}:",
                        repr(e),
                        flush=True
                    )

        except Exception as e:
            print(
                "ERRORE MONITOR TROFEI:",
                repr(e),
                flush=True
            )

        time.sleep(6 * 60 * 60)



def create_trophy_chart(player_tag, player_name, history, days=30):
    try:
        if not history or len(history) < 2:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        dates = []
        trophies = []

        for row in history:
            try:
                dt = datetime.fromisoformat(
                    row["recorded_at"].replace("Z", "+00:00")
                )

                if dt >= cutoff:
                    dates.append(dt)
                    trophies.append(int(row["trophies"]))

            except Exception:
                continue

        if len(dates) < 2:
            return None

        fig, ax = plt.subplots(figsize=(10, 5))

        ax.plot(
            dates,
            trophies,
            marker="o",
            linewidth=2
        )

        safe_name = player_name.encode(
            "ascii",
            "replace"
        ).decode("ascii")

        ax.set_title(
            f"Andamento trofei - {safe_name}"
        )

        ax.set_xlabel("Data")
        ax.set_ylabel("Trofei")
        ax.grid(True, alpha=0.3)

        same_day = dates[0].date() == dates[-1].date()

        if same_day:
            ax.xaxis.set_major_formatter(
                mdates.DateFormatter("%d/%m %H:%M")
            )
        else:
            ax.xaxis.set_major_formatter(
                mdates.DateFormatter("%d/%m")
            )

        min_trophies = min(trophies)
        max_trophies = max(trophies)

        if min_trophies == max_trophies:
            margin = 50
        else:
            margin = max(
                20,
                int((max_trophies - min_trophies) * 0.20)
            )

        ax.set_ylim(
            min_trophies - margin,
            max_trophies + margin
        )

        fig.autofmt_xdate()
        fig.tight_layout()

        image = io.BytesIO()

        fig.savefig(
            image,
            format="png",
            dpi=150
        )

        plt.close(fig)

        image.seek(0)
        image.name = f"{player_tag}_trofei.png"

        return image

    except Exception as e:
        print("ERRORE GRAFICO TROFEI:", repr(e), flush=True)
        return None


def get_brawlzone_player(player_tag):
    try:
        tag = player_tag.upper().replace("#", "").strip()

        if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag):
            return None

        response = requests.get(
            f"https://brawlzone.net/player/{tag}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )

        if response.status_code != 200:
            return None

        decoded = html.unescape(response.text)

        title_match = re.search(
            rf"<title>(.*?) \(#{re.escape(tag)}\) · BrawlZone</title>",
            decoded,
            re.I | re.S
        )

        description_match = re.search(
            r"has ([\d,.]+) trophies and (\d+) brawlers",
            decoded,
            re.I
        )

        if not title_match or not description_match:
            return None

        def number(value):
            return int(re.sub(r"\D", "", value))

        def find_stat(label):
            match = re.search(
                rf"children\\\":\\\"{re.escape(label)}\\\".*?children\\\":\\\"([\d,.]+)\\\"",
                decoded,
                re.I | re.S
            )
            return number(match.group(1)) if match else None

        level_match = re.search(
            r"\\\"Level \\\",\s*(\d+)",
            decoded
        )

        prestige_match = re.search(
            r"\\\"Prestige \\\",\s*\\\"?(\d+)",
            decoded
        )

        return {
            "name": title_match.group(1).strip(),
            "tag": f"#{tag}",
            "trophies": number(description_match.group(1)),
            "brawlers": int(description_match.group(2)),
            "level": int(level_match.group(1)) if level_match else None,
            "prestige": int(prestige_match.group(1)) if prestige_match else None,
            "wins_3v3": find_stat("3v3 wins"),
            "wins_solo": find_stat("Solo SD wins"),
            "wins_duo": find_stat("Duo SD wins")
        }

    except Exception as e:
        print("ERRORE BRAWLZONE PLAYER:", repr(e), flush=True)
        return None


def format_number_it(value):
    if value is None:
        return "Non disponibile"

    return f"{value:,}".replace(",", ".")


community = CommunityFeatures(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    get_brawlzone_player,
    get_trophy_history,
    calculate_trophy_changes,
    format_number_it,
    format_trophy_change,
)


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    community.track_activity(message)

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

    if await community.continue_registration(message, context):
        return

    if await community.continue_recruitment(message, context):
        return

    if await community.handle_command(message, context, question):
        return

    chart_match = re.fullmatch(
        r"grafico(?:\s+(7|15|30|90))?\s+#?([0289PYLQGRJCUV]{3,15})",
        question.strip(),
        re.I
    )

    if chart_match:
        days = int(chart_match.group(1) or 30)
        player_tag = chart_match.group(2).upper()

        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action="upload_photo"
        )

        player = get_brawlzone_player(player_tag)

        if not player:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text="Giocatore non trovato."
            )
            return

        save_trophy_snapshot(
            player["tag"],
            player["name"],
            player["trophies"]
        )

        history = get_trophy_history(
            player["tag"],
            days=max(days, 90)
        )

        chart = create_trophy_chart(
            player["tag"],
            player["name"],
            history,
            days=days
        )

        if not chart:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=(
                    f"Non ci sono ancora abbastanza dati per creare "
                    f"il grafico degli ultimi {days} giorni."
                )
            )
            return

        await context.bot.send_photo(
            chat_id=message.chat_id,
            photo=chart,
            caption=(
                f"Andamento trofei di {player["name"]}\n"
                f"Periodo: ultimi {days} giorni\n"
                f"Trofei attuali: {format_number_it(player["trophies"])}"
            )
        )
        return

    stats_match = re.fullmatch(
        r"(?:stats|profilo|scheda)\s+#?([0289PYLQGRJCUV]{3,15})",
        question.strip(),
        re.I
    )

    if stats_match:
        player_tag = stats_match.group(1).upper()

        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action="typing"
        )

        player = get_brawlzone_player(player_tag)

        if not player:
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=(
                    "Non riesco a trovare questo giocatore.\n"
                    "Controlla che il tag sia corretto e riprova."
                )
            )
            return

        save_trophy_snapshot(
            player["tag"],
            player["name"],
            player["trophies"]
        )

        history = get_trophy_history(
            player["tag"],
            days=91
        )

        changes = calculate_trophy_changes(
            history,
            player["trophies"]
        )

        member_data = community.get_member_by_player_tag(
            message.chat_id,
            player["tag"]
        )

        ranked_current = (
            (member_data or {}).get("ranked_current")
            or player.get("ranked_current")
            or "Non disponibile"
        )
        ranked_peak = (
            (member_data or {}).get("ranked_peak")
            or player.get("ranked_peak")
            or "Non disponibile"
        )

        text = (
            f"{player['name']}\n"
            f"Tag: {player['tag']}\n\n"
            f"Trofei: {format_number_it(player['trophies'])}\n"
            f"Brawler: {format_number_it(player['brawlers'])}\n"
            f"Livello: {format_number_it(player['level'])}\n"
            f"Prestigio: {format_number_it(player['prestige'])}\n"
            f"Ranked attuale: {ranked_current}\n"
            f"Ranked massima: {ranked_peak}\n\n"
            f"Vittorie:\n"
            f"- 3v3: {format_number_it(player['wins_3v3'])}\n"
            f"- Solo: {format_number_it(player['wins_solo'])}\n"
            f"- Duo: {format_number_it(player['wins_duo'])}\n\n"
            f"Andamento trofei:\n"
            f"- Oggi: {format_trophy_change(changes.get('today'))}\n"
            f"- 7 giorni: {format_trophy_change(changes.get('7d'))}\n"
            f"- 15 giorni: {format_trophy_change(changes.get('15d'))}\n"
            f"- 30 giorni: {format_trophy_change(changes.get('30d'))}\n"
            f"- 90 giorni: {format_trophy_change(changes.get('90d'))}"
        )

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=text
        )
        return

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

            search_results = search_data.get("results", [])

            if is_current_meta_query(question_for_ai):
                search_results = sorted(
                    search_results,
                    key=lambda result: meta_source_priority(
                        result.get("url", "")
                    )
                )

            for result in search_results:
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

            current_map = extract_brawl_ball_map(search_data)
            verified_comp = []

            if current_map:
                verified_comp = get_verified_map_comp(current_map)

            if current_map:
                try:
                    comp_data = search_map_comp(current_map)

                    web_context += f"\nDATI META PER MAPPA: {current_map}\n"


                    for result in comp_data.get("results", []):
                        title = result.get("title", "")
                        content = result.get("content", "")
                        raw_content = result.get("raw_content", "") or ""
                        url = result.get("url", "")

                        combined_text = (title + "\n" + content + "\n" + raw_content).lower()
                        map_name_lower = current_map.lower()
                        url_lower = url.lower()

                        if map_name_lower not in combined_text or "brawl ball" not in combined_text:
                            print("FONTE COMP SCARTATA:", url, flush=True)
                            continue

                        if "brawltime.ninja/tier-list/mode/brawl-ball" in url_lower and "/map/" not in url_lower:
                            print("FONTE COMP GENERICA SCARTATA:", url, flush=True)
                            continue

                        if title or content:
                            web_context += (
                                f"Titolo: {title}\n"
                                f"Contenuto: {content}\n"
                                f"Contenuto completo: {raw_content[:5000]}\n"
                                f"Fonte: {url}\n"
                                f"---\n"
                            )

                        if url:
                            web_sources.append(url)

                    print("MAPPA ESTRATTA:", current_map, flush=True)

                except Exception as e:
                    print("ERRORE RICERCA COMP MAPPA:", repr(e), flush=True)


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

                "USA LE INFORMAZIONI WEB FORNITE.\nNON MOSTRARE MAI LE FONTI, GLI URL O I LINK ALL UTENTE.\n\n"
                "REGOLE PER META E DATI ATTUALI:\n"
                "- Se la domanda riguarda meta, tier list, Ranked, migliori Brawler, pick rate, win rate, buff, nerf o bilanciamenti attuali, considera la ricerca web come obbligatoria.\n"
                "- Per affermazioni sul meta attuale NON usare la memoria interna del modello come fonte principale.\n"
                "- Dai priorità ai risultati più recenti e coerenti con l'ultima patch o stagione verificabile.\n"
                "- Gerarchia fonti per il meta: 1) Supercell/Brawl Stars ufficiale per patch, buff, nerf e modifiche; 2) Brawlify e Brawl Time Ninja per statistiche, tier list e andamento competitivo; 3) Noff come supporto; 4) altre fonti community solo come conferma secondaria.\n"
                "- Una fonte ufficiale stabilisce cosa è cambiato, ma il meta reale va valutato anche con statistiche e dati competitivi aggiornati.\n"
                "- Confronta più risultati quando possibile: non dichiarare un Brawler 'meta' basandoti su una sola fonte debole.\n"
                "- Se i risultati web non permettono di verificare il meta attuale con sufficiente affidabilità, dichiaralo chiaramente invece di indovinare.\n"
                "- Se l'utente chiede cosa pushare o come pushare un Brawler, struttura la risposta con: modalità consigliate, mappe favorevoli attuali se verificabili, configurazione consigliata, comp/sinergie, matchup da evitare e un piano pratico di push.\n"
                "- DISTINZIONE LADDER/CLASSIFICATA OBBLIGATORIA:\n"
                "  - Ladder/trofei: usa esclusivamente statistiche trophy-ladder e mappe della rotazione a trofei attuale.\n"
                "  - Classificata/Ranked: usa esclusivamente statistiche Ranked e mappe del pool Classificata attuale; considera draft, ban, counterpick e sinergie.\n"
                "  - Non mescolare mai percentuali Ladder e Ranked nella stessa raccomandazione.\n"
                "  - Se la domanda non specifica Ladder o Classificata e i due contesti portano a consigli diversi, separa la risposta in due sezioni: Ladder e Classificata.\n"
                "  - Se una mappa è indicata come solo Ranked, non proporla per Ladder. Se è archiviata o fuori pool, non proporla come attuale.\n"
                "- Per statistiche per mappa usa Brawl Planet come fonte prioritaria quando disponibile: distingue Ladder e Ranked e mostra tasso di vittoria, utilizzo, giocatore stella e composizioni.\n"
                "- Non scegliere automaticamente il Brawler con il win rate più alto: valuta insieme tasso di vittoria, tasso di utilizzo, frequenza giocatore stella, numero di partite/campione e qualità delle composizioni.\n"
                "- Diffida di percentuali molto alte con utilizzo o campione molto basso; preferisci dati robusti e coerenti tra più indicatori.\n"
                "- TERMINOLOGIA ITALIANA OBBLIGATORIA: usa sempre i termini ufficiali del gioco in italiano. Gear = equipaggiamento/equipaggiamenti; Star Power = abilità stellare/abilità stellari; Hypercharge = overdrive; Gadget resta gadget.\n"
                "- Per gadget, abilità stellari, equipaggiamenti e overdrive specifici usa il NOME UFFICIALE ITALIANO mostrato in Brawl Stars, non il nome inglese.\n"
                "- Per i nomi localizzati dai priorità alle pagine italiane ufficiali di Supercell e alle fonti italiane affidabili.\n"
                "- Se il nome italiano ufficiale di una specifica abilità non è verificabile, non tradurlo a intuito e non mostrare il nome inglese: descrivi semplicemente l effetto o il tipo di scelta consigliata.\n"
                "- Questa regola vale per tutti gli elementi del gioco mostrati all utente: modalità, mappe, gadget, abilità stellari, equipaggiamenti, overdrive, eventi e oggetti.\n\n"

                + (
                    f"MAPPA CORRENTE IDENTIFICATA DAL SISTEMA: {current_map}\n"
                    "- Se la domanda riguarda la mappa corrente, scrivi sempre questo nome esatto nella risposta.\n"
                    "- Non sostituire questa mappa con nomi trovati in altre fonti.\n"
                    "- Se proponi una composizione, deve riferirsi esclusivamente a questa mappa e alla modalità richiesta.\n\n"
                    if "current_map" in locals() and current_map
                    else ""
                )

                + "Regole fondamentali:\n"
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
                "- Usa le fonti web internamente per verificare i dati, ma NON mostrare fonti, URL, link o una sezione Fonti nella risposta.\n"
                "- Quando consigli una mappa specifica attualmente disponibile, aggiungi una riga tecnica: MAPPA_IMMAGINE: NomeMappa.\n"
                "- In MAPPA_IMMAGINE usa il nome esatto della mappa trovato nelle fonti web; preferisci il nome inglese/canonico della fonte per permettere al sistema di recuperare l immagine corretta.\n"
                "- Inserisci MAPPA_IMMAGINE solo se quella mappa è stata verificata come attuale/disponibile; non usarla per mappe storiche o non verificate.\n"
                "- La riga MAPPA_IMMAGINE è un dato tecnico e verrà rimossa prima di mostrare la risposta all utente.\n"
                "- La risposta deve sembrare scritta da un assistente ufficiale della community, non da un chatbot che cerca di essere simpatico.\n\n"
                "FONTE PRIORITARIA PER LE MAPPE E LE STATISTICHE:\n"
                "- Per nomi italiani delle mappe e statistiche specifiche per mappa usa Brawl Planet come fonte prioritaria quando disponibile.\n"
                "- Brawl Planet separa Ladder e Classificata: usa sempre il dataset coerente con la domanda dell utente.\n"
                "- Per verificare la rotazione live delle mappe usa anche Brawl Insights come fonte primaria di rotazione.\n"
                "- La fonte primaria per la rotazione è https://brawlinsights.com/en/tools/map_rotation.\n"
                "- Se Brawl Insights non permette di verificare la mappa corrente, usa Brawlify come fonte di fallback live.\n"
                "- Per il fallback live usa https://brawlify.com/it/maps.\n"
                "- Usa Brawlify solo se mostra chiaramente la rotazione corrente; non usare mappe storiche come se fossero attive.\n"
                "- Non usare mappe storiche o risultati provenienti da altre fonti per dichiarare quale mappa è attiva se Brawl Insights fornisce il dato.\n"
                "- Se la rotazione attuale non è verificabile, dichiaralo chiaramente e non indovinare.\n"
                "- Quando l utente chiede su quale mappa usare un Brawler, NON proporre mappe storiche, rimosse o fuori dal pool attuale.\n"
                "- Una mappa può essere consigliata solo se i risultati web aggiornati mostrano che è attualmente disponibile nella rotazione o nel pool della modalità pertinente.\n"
                "- Se una buona mappa per quel Brawler esiste storicamente ma non è disponibile adesso, non consigliarla come scelta attuale.\n"
                "- Se non riesci a verificare almeno una mappa attualmente disponibile, consiglia la modalità e spiega che la mappa attiva non è verificabile, senza inventare.\n\n"

                "- I nomi delle mappe mostrati all utente devono essere SEMPRE quelli ufficiali italiani usati nel gioco.\n"
                "- Le fonti web possono contenere i nomi inglesi: usali solo internamente per la ricerca e non mostrarli nella risposta se esiste il nome ufficiale italiano.\n"
                "- Non inventare traduzioni di nomi ufficiali.\n"
                "- Per una miglior composizione identifica prima la mappa corrente e poi scegli i Brawler più adatti a quella specifica mappa.\n\n"
                "- Se è stata identificata una mappa specifica, usa solo dati e statistiche relativi a quella mappa per scegliere la composizione.\n"
                "- Non usare tier list, win rate o composizioni generali della modalità come prova della miglior comp per una mappa specifica.\n"
                "- Se non trovi dati affidabili per quella specifica mappa, dichiaralo chiaramente invece di sostituirli con dati generali della modalità.\n"
                "- Per usare una fonte come prova della miglior comp, verifica che nella fonte compaiano sia il nome esatto della mappa sia la modalità esatta richiesta.\n"
                "- Non associare statistiche di una mappa usata in un altra modalità alla modalità richiesta dall utente.\n"
                "- Brawl Planet, Brawl Insights, Brawl Time Ninja, Noff, Brawlify e Power League Prodigy sono fonti community/statistiche: non definirle fonti ufficiali Supercell.\n"
                "- Quando l utente chiede la miglior comp, indica esattamente 3 Brawler specifici.\n"
                "- Non rispondere con categorie generiche come tank, tiratori, supporti o brawler da mischia.\n"
                "- Se i dati disponibili non permettono di determinare una comp affidabile, dichiaralo chiaramente e non inventare.\n"

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

                "Usa sempre la terminologia ufficiale italiana di Brawl Stars. "
                "Scrivi equipaggiamento invece di gear, abilità stellare invece di Star Power "
                "e overdrive invece di Hypercharge. Per i nomi specifici di gadget, abilità "
                "stellari, equipaggiamenti e overdrive usa solo il nome italiano ufficiale; "
                "se non sei sicuro del nome ufficiale, descrivi l effetto senza inventare una traduzione.\n\n"

                f"DOMANDA E CONTESTO:\n"
                f"{question_for_ai}"
            )

        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=instructions
        )

        final_text = response.text or ""
        comp_brawlers = []
        recommended_map = None

        match_map = re.search(
            r"^MAPPA_IMMAGINE:\s*(.+)$",
            final_text,
            re.I | re.M
        )

        if match_map:
            recommended_map = resolve_map_source_name(
                match_map.group(1).strip()
            )

            final_text = re.sub(
                r"^MAPPA_IMMAGINE:\s*.+$",
                "",
                final_text,
                flags=re.I | re.M
            ).strip()

            print(
                "MAPPA IMMAGINE ESTRATTA:",
                recommended_map,
                flush=True
            )

        match_brawlers = re.search(
            r"^BRAWLERS_IMMAGINI:\s*(.+)$",
            final_text,
            re.I | re.M
        )

        if match_brawlers:
            comp_brawlers = [
                name.strip()
                for name in match_brawlers.group(1).split("|")
                if name.strip()
            ]

            final_text = re.sub(
                r"^BRAWLERS_IMMAGINI:\s*.+$",
                "",
                final_text,
                flags=re.I | re.M
            ).strip()

            print(
                "BRAWLERS COMP ESTRATTI:",
                comp_brawlers,
                flush=True
            )

        final_text = re.split(
            r"\n\s*(?:Fonti|Fonti utilizzate|Sources)\s*:?\s*",
            final_text,
            maxsplit=1,
            flags=re.I
        )[0].strip()

        if comp_brawlers:
            comp_brawlers = [
                name
                for name in comp_brawlers
                if re.search(
                    r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
                    final_text,
                    re.I
                )
            ][:3]

            print(
                "BRAWLERS IMMAGINI CONFERMATI NELLA RISPOSTA:",
                comp_brawlers,
                flush=True
            )

        final_text = re.sub(
            r"https?://\S+",
            "",
            final_text,
            flags=re.I
        ).strip()

        # Converte i nomi inglesi delle mappe e la terminologia generica
        # nei termini ufficiali italiani prima di mostrare la risposta.
        final_text = translate_map_names_in_text(final_text)
        final_text = translate_game_terms_in_text(final_text)

        if "verified_comp" in locals() and len(verified_comp) == 3:
            comp_brawlers = verified_comp[:3]

            display_map = map_name_it(current_map)

            final_text = (
                f"FOOTBRAWL - {display_map.upper()}\n\n"
                "Comp consigliata:\n"
                + "\n".join(
                    brawler_name_it(name)
                    for name in comp_brawlers
                )
                + "\n\nMeta aggiornato."
            )

            print(
                "RISPOSTA COMP BLOCCATA SU:",
                comp_brawlers,
                flush=True
            )


        map_photo_sent = False

        map_to_send = None
        if "current_map" in locals() and current_map:
            map_to_send = current_map
        elif recommended_map:
            map_to_send = recommended_map

        if map_to_send:
            try:
                map_image = get_noff_map_image(map_to_send)

                if map_image:
                    image_response = requests.get(
                        map_image,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=15
                    )
                    image_response.raise_for_status()

                    await context.bot.send_photo(
                        chat_id=message.chat_id,
                        photo=image_response.content,
                        caption=f"Mappa: {map_name_it(map_to_send)}"
                    )

                    map_photo_sent = True
                    print(
                        "IMMAGINE MAPPA INVIATA:",
                        map_to_send,
                        flush=True
                    )
                else:
                    print(
                        "NESSUNA IMMAGINE DISPONIBILE PER MAPPA:",
                        map_to_send,
                        flush=True
                    )

            except Exception as e:
                print(
                    "ERRORE INVIO IMMAGINE MAPPA:",
                    repr(e),
                    flush=True
                )

        if "current_map" in locals() and current_map:
            final_text = re.sub(
                r"^" + re.escape(current_map) + r"\s*",
                "",
                final_text,
                count=1,
                flags=re.I
            ).strip()

            final_text = re.sub(
                r"Per\s+la\s+mappa\s+" + re.escape(current_map),
                "Per questa mappa",
                final_text,
                flags=re.I
            )

            final_text = re.sub(
                re.escape(current_map),
                "questa mappa",
                final_text,
                flags=re.I
            )

        final_text = final_text.replace("*", "").strip()

        await context.bot.send_message(
            chat_id=message.chat_id,
            text=final_text,
            disable_web_page_preview=True
        )

        if False and any(k in question_for_ai.lower() for k in ["mappa", "mappe", "rotazione"]):
            try:
                web_images = []
                if current_map:
                    map_image_data = image_search(f"Brawl Stars map {current_map}")
                    map_key = re.sub(r"[^a-z0-9]+", "", current_map.lower())
                    for image in map_image_data.get("images", []):
                        if isinstance(image, str):
                            image_url = image
                            description = ""
                        elif isinstance(image, dict):
                            image_url = image.get("url") or image.get("image_url")
                            description = image.get("description") or image.get("title") or ""
                        else:
                            continue
                        if not image_url or not image_url.startswith("http"):
                            continue
                        image_url_key = re.sub(r"[^a-z0-9]+", "", image_url.lower())
                        description_key = re.sub(r"[^a-z0-9]+", "", description.lower())

                        if (
                            map_key
                            and (
                                map_key in image_url_key
                                or map_key in description_key
                            )
                        ):
                            web_images.append({
                                "url": image_url,
                                "description": description
                            })
                            print(
                                "IMMAGINE MAPPA ACCETTATA:",
                                current_map,
                                image_url,
                                flush=True
                            )
                            break

                        print(
                            "IMMAGINE MAPPA SCARTATA:",
                            image_url,
                            description,
                            flush=True
                        )
            except Exception as e:
                print("ERRORE IMMAGINE MAPPA:", repr(e), flush=True)


        explicit_image_request = any(
            k in question_for_ai.lower()
            for k in [
                "foto",
                "immagine",
                "immagini",
                "mostrami",
                "fammi vedere"
            ]
        )

        is_map_or_comp_request = any(
            k in question_for_ai.lower()
            for k in [
                "mappa",
                "mappe",
                "rotazione",
                "miglior comp",
                "migliore comp",
                "composizione"
            ]
        )

        if explicit_image_request and not is_map_or_comp_request:
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

    if application.job_queue:
        application.job_queue.run_repeating(
            community.scheduled_jobs,
            interval=3600,
            first=45,
            name="community_jobs"
        )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer
        )
    )

    port = int(os.environ.get("PORT", 10000))

    base_url = os.environ.get(
        "RENDER_EXTERNAL_URL",
        "https://sens-gpt-telegram.onrender.com"
    ).rstrip("/")

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="telegram",
        webhook_url=f"{base_url}/telegram"
    )


if __name__ == "__main__":
    threading.Thread(
        target=automatic_trophy_monitor,
        daemon=True
    ).start()

    main()
