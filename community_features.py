import os
import base64
import hashlib
import re
import logging
import io
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote

import requests

from trophy_coefficient import calculate_trophy_coefficient
from coefficient_guide import coefficient_guide_lines
from skin_pass_provenance import is_verified_brawl_pass_skin

ROME = ZoneInfo("Europe/Rome")
LOG = logging.getLogger(__name__)

_SKIN_BRIDGE_FAILURES = 0
_SKIN_BRIDGE_OPEN_UNTIL = 0.0
_SKIN_BRIDGE_FAILURE_LIMIT = 3
_SKIN_BRIDGE_BACKOFF_SECONDS = 6 * 60 * 60
_SKIN_DIRECT_OPEN_UNTIL = 0.0
_SKIN_CATEGORY_URLS = {}
_SKIN_CATEGORY_URLS_LOCK = threading.Lock()
_DASHBOARD_CACHE = {}
_DASHBOARD_CACHE_LOCK = threading.Lock()
_DASHBOARD_FORMAT_REVISION = 8
_DASHBOARD_SOURCE_MARKER = "Liste: valori positivi verificati · copertura club e coefficiente medio nel Resoconto."
_PROGRESSION_DETAIL_CACHE = {}
_PROGRESSION_DETAIL_LOCK = threading.Lock()
_PROGRESSION_DETAIL_FLIGHTS = {}
_TELEGRAPH_PAGE_LOCK = threading.Lock()

PROGRESSION_MODE_NAMES_IT = {
    "gemgrab": "Arraffagemme",
    "brawlball": "Footbrawl",
    "hotzone": "Dominio",
    "bounty": "Ricercati",
    "heist": "Rapina",
    "knockout": "K.O.",
    "soloshowdown": "Sopravvivenza in singolo",
    "duoshowdown": "Sopravvivenza in duo",
    "trioshowdown": "Sopravvivenza in trio",
    "showdown": "Sopravvivenza",
    "duels": "Duelli",
    "wipeout": "Annientamento",
    "deathmatch5v5": "Annientamento 5v5",
    "payload": "Corsa dei carrelli",
    "basketbrawl": "Basket Brawl",
    "volleybrawl": "Volley Brawl",
    "trophythieves": "Ladri di trofei",
    "presentplunder": "Furto dei regali",
    "holdthetrophy": "Tieni il trofeo",
    "siege": "Assedio",
    "hunters": "Cacciatori",
    "lonestar": "Stella solitaria",
    "takedown": "Eliminazione",
    "botdrop": "Caduta di robot",
    "laststand": "Ultima resistenza",
    "biggame": "Megabrawl",
    "bossfight": "Boss Fight",
    "roborumble": "Sfida al Boss Robot",
    "airhockey": "Brawl Hockey",
    "brawlarena": "Arena dei Brawler",
    "paintbrawl": "Brawl Pittura",
}

PROGRESSION_RESULT_NAMES_IT = {
    "victory": "Vittoria",
    "defeat": "Sconfitta",
    "draw": "Pareggio",
}

PROGRESSION_BONUS_NAMES_IT = {
    "win_streak": "Serie di vittorie",
    "underdog": "Sfavorito",
    "bot": "Battaglia contro bot",
    "low_trophy": "Bonus trofei bassi",
    "combined": "Bonus combinato",
    "bonus_observed": "Bonus osservato",
    "win_streak_observed": "Serie di vittorie osservata",
    "underdog_observed": "Sfavorito osservato",
}


class SkinBridgeBackoff(RuntimeError):
    """Raised while the external skin source circuit breaker is open."""

FAQ_TEXT = (
    "TITANI ABUSIVI - INFO RAPIDE\n\n"
    "- Club competitivo: minimo 100.000 trofei.\n"
    "- Ranked minimo: Leggenda.\n"
    "- I club della community, in ordine, sono: TITANI ABUSIVI, TAMARRI ABUSIVI, TORNADI ABUSIVI, TALENTI ABUSIVI.\n"
    "- In base a requisiti, attività e disponibilità posti puoi essere spostato tra i quattro club.\n"
    "- Gli eventi dichiarati obbligatori, come il Megasalvadanaio, vanno completati: chi non partecipa può essere espulso indipendentemente da coppe o ruolo.\n"
    "- Telegram e Discord sono obbligatori quando richiesti per tornei/eventi.\n"
    "- Se sei assente per studio, lavoro o vacanze avvisa la direzione o usa il comando assenza.\n"
    "- Reclutamento: titaniabusivi.it"
)

HELP_TEXT = """COMANDI SENS GPT — GUIDA COMPLETA

Questa pagina raccoglie i comandi disponibili. Tocca un nome per aprire la guida Telegraph della sua categoria; per eseguire un comando personale scrivilo al bot.

👤 ACCOUNT E PROFILO
[[CMDNAME:registrami #TAG]]
Scrivi nel gruppo. Collega il tuo account Brawl Stars principale al tuo utente Telegram. Esempio: registrami #2LVRCLV8LV

[[CMDNAME:aggiungi account #TAG]]
Collega un account Brawl Stars secondario o successivo al tuo profilo.

[[CMDNAME:i miei account]]
Mostra l'account principale e tutti gli account aggiuntivi collegati.

[[CMDNAME:profilo #TAG / stats #TAG]]
Genera la scheda completa del giocatore indicato. Senza #TAG, Stats usa il tuo account registrato.

[[CMDNAME:ranked #TAG]]
Mostra la Classificata attuale e i record Ranked disponibili del giocatore.

[[CMDNAME:storico ranked #TAG]]
Mostra le variazioni Ranked registrate nel tempo.

🎨 SKIN ACCOUNT
[[CMDNAME:skin / quante skin ho]]
Apre in privato il Telegraph con il conteggio rilevato da Stats e le categorie disponibili. Se Stats non risponde, viene mostrato l'ultimo conteggio Stats salvato con la sua data. Dove Stats non distingue le skin, il conteggio posseduto è indicato come n.d.

[[CMDNAME:skin NOME_BRAWLER]]
Mostra per categoria le Skin possedute e mancanti del Brawler indicato, con i nomi delle Skin.

[[CMDNAME:skin account NOME_BRAWLER]]
Mostra la situazione Skin completa del Brawler indicato.

[[CMDNAME:quante skin ho di NOME_BRAWLER]]
Conta quante Skin possiedi per quel Brawler.

[[CMDNAME:quali skin di NOME_BRAWLER ho]]
Elenca le Skin possedute del Brawler.

[[CMDNAME:quali skin di NOME_BRAWLER mi mancano]]
Elenca le Skin mancanti del Brawler.

[[CMDNAME:quante skin RARITÀ ho]]
Conta le Skin possedute nella categoria o rarità indicata.

[[CMDNAME:quali skin RARITÀ ho / mi mancano]]
Elenca possedute o mancanti filtrando per categoria o rarità.

[[CMDNAME:grafico skin [RARITÀ] [7|15|30|60|90|180|365]]]
Mostra lo storico della collezione Skin nel periodo scelto.

[[CMDNAME:mostrami la skin NOME_SKIN di NOME_BRAWLER]]
Mostra l'immagine disponibile della Skin specificata.

🏆 CLASSIFICHE & REPORT
[[CMDNAME:Classifica]]
Apre l'indice Telegraph generale di OGGI, 7, 15 e 30 giorni. Il messaggio resta breve: il Resoconto si vede chiedendo il singolo periodo.

[[CMDNAME:Classifiche]]
Sinonimo di Classifica: apre lo stesso indice generale.

📅 ACCESSI RAPIDI PER PERIODO
[[CMDNAME:classifiche oggi]]
Mostra il Resoconto di oggi in privato e nel Telegraph, nell'ordine: Trofei globali, Progressione Globale Club, 4 Club.

[[CMDNAME:classifiche 7]]
Mostra il Resoconto dei 7 giorni in privato e le tre classifiche cliccabili nello stesso ordine.

[[CMDNAME:classifiche 15]]
Mostra Resoconto e classifiche cliccabili dei 15 giorni.

[[CMDNAME:classifiche 30]]
Mostra Resoconto e classifiche cliccabili dei 30 giorni.

Sono accettate anche le forme Classifica 7, Classifica 15 e Classifica 30. Gli invii automatici pubblicano Resoconto e indice con link Telegraph diretti alle 06:00, 12:00, 18:00 e 23:59 per oggi; ogni lunedì alle 06:00 per la settimana conclusa; il 16 alle 06:00 per i giorni 1–15; l'ultimo giorno del mese alle 23:59 per i giorni 16–fine mese; il 1° alle 06:00 per il mese solare precedente.
Il Coeff. medio Progressione nel Resoconto è Progressione complessiva divisa per Coppe positive dello stesso periodo e ambito; senza coppe positive non è calcolabile.
Per le classifiche Trofei dei 4 Club si usa il roster completo: la crescita del periodo si calcola solo quando esistono misure reali prima dell'inizio e alla fine. Ogni club indica quanti giocatori hanno uno storico sufficiente rispetto al roster completo. Nelle liste compaiono solo crescite positive.

⚡ COMANDI DIRETTI DI OGGI
[[CMDNAME:classifica oggi]]
Mostra in privato il Resoconto di oggi e apre il Telegraph giornaliero con le tre classifiche cliccabili.

[[CMDNAME:progressione oggi]]
Mostra subito in privato la tua Progressione di oggi, con dettaglio dei Brawler nel Telegraph.

I comandi specifici dei singoli ambiti e periodi restano disponibili per l'uso avanzato.

[[CMD:cmd_classifica|Classifica]]
Mostra i periodi e le classifiche disponibili.

[[CMDNAME:classifica della community 7 / 15 / 30]]
Mostra direttamente la sola classifica Trofei dei registrati nel periodo scelto.

[[CMDNAME:classifica trofei]]
Ordina i giocatori per trofei attuali.

[[CMDNAME:classifica brawler]]
Confronta il numero di Brawler dei giocatori.

[[CMDNAME:classifica livello]]
Ordina i giocatori per livello account disponibile.

[[CMDNAME:classifica prestigio]]
Ordina i giocatori in base al Prestigio Brawler registrato.

[[CMDNAME:classifica 3v3]]
Ordina i giocatori per vittorie 3v3.

[[CMDNAME:classifica solo]]
Ordina i giocatori per vittorie Sopravvivenza Solo.

[[CMDNAME:classifica duo]]
Ordina i giocatori per vittorie Sopravvivenza Duo.

[[CMDNAME:classifica classificata / classifica ranked]]
Mostra la classifica Ranked attuale.

[[CMDNAME:classifica ranked oggi / 7 / 15 / 30]]
Mostra la variazione Ranked/ELO osservata nel periodo.

[[CMDNAME:classifica ranked stagione]]
Confronta il record Ranked della stagione corrente disponibile.

[[CMDNAME:classifica ranked carriera]]
Confronta il record Ranked massimo di carriera disponibile.

[[CMDNAME:coefficiente abusivo #TAG]]
Calcola il Coefficiente Abusivo strutturale del giocatore, separato dalla Progressione del periodo.

[[CMD:cmd_progressione|Progressione]]
[[CMD:cmd_progressione_oggi|Progressione oggi]]
[[CMD:cmd_progressione_7|Progressione 7 giorni]]
[[CMD:cmd_progressione_15|Progressione 15 giorni]]
[[CMD:cmd_progressione_30|Progressione 30 giorni]]
Mostra il riepilogo Progressione del giocatore e tutti i Brawler realmente giocati nel periodo.

[[CMDNAME:progressione NOME_BRAWLER [oggi|7|15|30] [#TAG]]]
Analizza un singolo Brawler: partite, vittorie, sconfitte, pareggi, win rate, coppe positive/perse, saldo, Bonus, Progressione e Coeff. Progressione. Esempio: progressione Brock 7

[[CMD:cmd_guida_progressione|Guida Progressione]]
Apre la guida Telegraph dedicata e spiega esattamente come viene calcolata la Progressione.

[[CMDNAME:classifica progressione [community|globale|club] [oggi|7|15|30]]]
Ordina i giocatori usando la Progressione ponderata nel periodo e mostra Coppe, Bonus e Coeff. Progressione.

[[CMDNAME:statistiche / tutte le classifiche]]
Mostra un riepilogo delle statistiche e classifiche disponibili.

🛡️ CLASSIFICHE DEI 4 CLUB
[[CMDNAME:classifica titani / tamarri / tornadi / talenti + METRICA]]
Applica una classifica al singolo club ABUSIVO. Esempi: classifica titani 3v3; classifica tamarri trofei; classifica tornadi prestigio; classifica talenti duo.

[[CMDNAME:statistiche titani / tamarri / tornadi / talenti]]
Mostra il riepilogo statistico del club scelto.

📊 GRAFICI
[[CMDNAME:grafico 7|15|30|90 #TAG]]
Mostra l'andamento storico del giocatore nel periodo.

[[CMDNAME:grafico community 7|15|30|90]]
Mostra l'andamento aggregato dei giocatori registrati.

[[CMDNAME:grafico titani 7|15|30|90]]
Mostra l'andamento del club TITANI ABUSIVI.

[[CMDNAME:grafico tamarri 7|15|30|90]]
Mostra l'andamento del club TAMARRI ABUSIVI.

[[CMDNAME:grafico tornadi 7|15|30|90]]
Mostra l'andamento del club TORNADI ABUSIVI.

[[CMDNAME:grafico talenti 7|15|30|90]]
Mostra l'andamento del club TALENTI ABUSIVI.

🎯 DRAFT RANKED — SOLO CHAT PRIVATA
[[CMD:cmd_draft_ranked|Draft Ranked]]
Avvia la Draft guidata. Puoi indicare una mappa e una fascia Ranked oppure scegliere una modalità e poi una mappa Ranked corrente.

[[CMDNAME:draft stato / draft riepilogo]]
Mostra ban, pick e stato corrente della Draft in corso.

[[CMDNAME:draft reset]]
Chiude la Draft corrente e azzera la sessione.

[[CMDNAME:counter NOME_BRAWLER]]
Mostra i counter verificati disponibili per il Brawler indicato.

🧠 MEMORIA CONVERSAZIONALE
Sens GPT mantiene un contesto personale separato per ogni utente per rendere il rapporto più coerente nel tempo. Nei gruppi può apprendere silenziosamente anche dalle normali conversazioni dei membri senza intervenire: risponde soltanto quando viene menzionato o quando si risponde a un suo messaggio. La memoria conversazionale non modifica Stats, classifiche, Progressione o altri comandi deterministici.

🤝 COMMUNITY
[[CMDNAME:club]]
Mostra il riepilogo della community e dei club configurati.

[[CMDNAME:elenco utenti]]
Mostra nel gruppo gli account della community collegati al bot. Funziona anche elenco registrati.

[[CMDNAME:elenco inattivi]]
Mostra nel gruppo la situazione di inattività disponibile al bot. Anche l'avviso automatico per la soglia kick resta nel gruppo.

[[CMDNAME:assenza N]]
Registra una segnalazione di assenza per il numero di giorni indicato.

[[CMDNAME:eventi]]
Mostra gli eventi community aperti.

[[CMDNAME:partecipo ID]]
Conferma la partecipazione all'evento identificato dall'ID.

📑 REPORT PERIODICI — TROFEI + PROGRESSIONE
I report Oggi/7/15/30 uniscono Classifica Trofei, Classifica Progressione e resoconto. Telegram mostra solo Classifica Trofei e Resoconto; il Telegraph contiene tutti i dettagli.

[[CMDNAME:report 7]]
Report 7 giorni degli utenti registrati.

[[CMDNAME:report 15]]
Report 15 giorni degli utenti registrati.

[[CMDNAME:report 30]]
Report 30 giorni degli utenti registrati.

[[CMDNAME:report club 7]]
[[CMDNAME:report club 15]]
[[CMDNAME:report club 30]]
Report degli utenti registrati appartenenti ai quattro club ABUSIVI.

[[CMDNAME:report globale club 7]]
[[CMDNAME:report globale club 15]]
[[CMDNAME:report globale club 30]]
Report del roster completo dei quattro club.

[[CMDNAME:report titani 7]]
[[CMDNAME:report titani 15]]
[[CMDNAME:report titani 30]]
Report dei registrati di TITANI ABUSIVI. Gli stessi comandi sono disponibili sostituendo titani con tamarri, tornadi o talenti.

[[CMDNAME:report club globale titani 7]]
[[CMDNAME:report club globale titani 15]]
[[CMDNAME:report club globale titani 30]]
Report del roster completo TITANI. Gli stessi comandi sono disponibili per tamarri, tornadi e talenti.

[[CMDNAME:report oggi]]
Mostra il resoconto operativo giornaliero. Per il report di classifiche e Progressione della community usa Report Community oggi; per gli altri ambiti usa Report Club oggi, Report Globale Club oggi o Report Titani/Tamarri/Tornadi/Talenti oggi.

[[CMDNAME:reclutamento]]
Avvia la procedura di candidatura/reclutamento.

[[CMDNAME:regole / faq]]
Mostra il regolamento e le risposte rapide della community.

[[CMDNAME:sito]]
Mostra il sito ufficiale della community.

[[CMDNAME:discord]]
Mostra il collegamento al server Discord della community.

🎮 BRAWL STARS — RICHIESTE LIBERE
Sens GPT può inoltre rispondere a richieste su meta, mappe, composizioni, Ladder o Classificata, Brawler, configurazioni, gadget, abilità stellari, equipaggiamenti, overdrive e consigli di gioco. Queste non sono sintassi rigide come i comandi sopra.

Scrivi comandi in qualsiasi momento per riaprire questa guida."""


MEMORY_ANNOUNCEMENT_TEXT = """🧠 NOVITÀ SENS GPT — MEMORIA PERSONALE

Sens GPT sta facendo un altro passo avanti: può costruire nel tempo una memoria personale e separata per ogni membro della community.

💬 CONVERSAZIONI DEL GRUPPO
Sens GPT può apprendere anche dalle normali conversazioni nel gruppo, senza interromperle e senza rispondere automaticamente. Quello che un membro racconta può contribuire a creare nel tempo un rapporto più coerente e personale con lui.

Per ottenere una risposta nel gruppo bisogna comunque:
• menzionare @SensGPT_TitaniAbusiviBot
• oppure rispondere direttamente a un suo messaggio.

🧠 CONTINUITÀ NEL TEMPO
Quando viene coinvolto, Sens GPT può utilizzare il contesto costruito nel tempo per evitare di comportarsi ogni volta come se fosse la prima conversazione.

👤 MEMORIA SEPARATA PER UTENTE
Ogni memoria è personale: Sens GPT distingue i membri attraverso il loro account Telegram e mantiene separato il contesto di ciascuno.

⚙️ COMANDI DETERMINISTICI SEPARATI
Stats, classifiche, Progressione, Coefficiente Abusivo, Draft, Skin e gli altri comandi deterministici restano separati dalla memoria conversazionale.

In questo modo Sens GPT può conoscere progressivamente le persone della community, ricordare il contesto delle conversazioni e sviluppare un rapporto differente con ciascun membro, continuando a rimanere silenzioso quando non viene chiamato.

🔥 Un altro passo per trasformare Sens GPT da semplice bot a AI della community TITANI ABUSIVI."""

PROGRESSION_GUIDE_TEXT = """🔥 GUIDA PROGRESSIONE — TITANI ABUSIVI

🏆 LA TUA PROGRESSIONE, BRAWLER PER BRAWLER
Un sistema pensato per leggere quanto vale davvero la tua scalata: ogni battaglia osservata viene analizzata usando i trofei del singolo Brawler nel momento in cui giochi.

⚙️ COME FUNZIONA
Ogni guadagno positivo di coppe viene pesato in base alla fascia trofei del Brawler usato.
Più impegnativa è la fascia, maggiore può essere il peso applicato a quelle coppe.

Le sconfitte restano registrate per partite, vittorie/sconfitte, saldo e analisi, ma non generano Progressione negativa.
Alla fine vengono sommati i punti prodotti da tutti i Brawler giocati: non viene fatta la media dei loro coefficienti.

🧮 FORMULA
Coeff. Progressione = Progressione totale / Coppe positive totali

Il Coeff. Progressione è dinamico e vale esclusivamente per il periodo selezionato.
Non va confuso con il Coefficiente Abusivo, che misura invece la struttura complessiva dell'account.

🗓️ SCEGLI IL PERIODO
Oggi — dalla mezzanotte italiana fino al momento del comando.
7 giorni — ultime 7 giornate mobili.
15 giorni — ultimi 15 giorni.
30 giorni — ultimi 30 giorni.

🔥 LA MIA PROGRESSIONE
Tocca uno dei comandi per eseguirlo direttamente in Sens GPT.

[[CMD:cmd_progressione|▶️ Progressione]]
Mostra il riepilogo personale del periodo predefinito.

[[CMD:cmd_progressione_oggi|▶️ Progressione oggi]]
Mostra quello che hai fatto dalla mezzanotte a ora.

[[CMD:cmd_progressione_7|▶️ Progressione 7 giorni]]
Analizza gli ultimi 7 giorni.

[[CMD:cmd_progressione_15|▶️ Progressione 15 giorni]]
Analizza gli ultimi 15 giorni.

[[CMD:cmd_progressione_30|▶️ Progressione 30 giorni]]
Analizza gli ultimi 30 giorni.

🎯 ANALIZZA UN SINGOLO BRAWLER
progressione NOME_BRAWLER
progressione NOME_BRAWLER oggi
progressione NOME_BRAWLER 7
progressione NOME_BRAWLER 15
progressione NOME_BRAWLER 30

Esempio: progressione Brock 7

Il dettaglio mostra:
🎮 Partite
✅ Vittorie
❌ Sconfitte
🤝 Pareggi
📊 Win rate
🏆 Coppe positive
📉 Coppe perse
📈 Saldo trofei
⚡ Bonus
🔥 Progressione
🧮 Coeff. Progressione
🏆 Trofei a inizio periodo e ultimo dato osservato
🎮 Log delle battaglie

🏅 CLASSIFICHE PROGRESSIONE
[[CMD:cmd_classifica_progressione_oggi|▶️ Classifica Progressione oggi]]
[[CMD:cmd_classifica_progressione_7|▶️ Classifica Progressione 7 giorni]]
[[CMD:cmd_classifica_progressione_15|▶️ Classifica Progressione 15 giorni]]
[[CMD:cmd_classifica_progressione_30|▶️ Classifica Progressione 30 giorni]]

Sono disponibili anche le classifiche Community, Club, Globale Club e quelle dedicate a TITANI, TAMARRI, TORNADI e TALENTI ABUSIVI.

📖 COME LEGGERE I VALORI
🏆 Coppe
Somma di tutte le coppe positive osservate nel periodo.

⚡ Bonus
Differenza tra Progressione e Coppe positive.

🔥 Progressione
Somma dei punti ponderati prodotti, battaglia dopo battaglia, da tutti i Brawler giocati.

🧮 Coeff. Progressione
Rapporto tra Progressione totale e Coppe positive totali.

💡 ESEMPIO SEMPLICE
Se nel periodo ottieni 500 Coppe positive e la Progressione calcolata è 575:
Bonus = +75
Coeff. Progressione = 1,150000

Più il coefficiente supera 1, più le coppe ottenute nel periodo sono state valorizzate dalle fasce trofei dei Brawler utilizzati.

📊 TABELLA PESI DEL COEFFICIENTE
0–49 → x1,0000
50–99 → x1,0250
100–199 → x1,0285
200–299 → x1,0320
300–499 → x1,0500
500–599 → x1,0590
600–799 → x1,0680
800–999 → x1,0970
1.000–1.099 → x1,1180
1.100–1.199 → x1,1360
1.200–1.299 → x1,1650
1.300–1.499 → x1,1900
1.500–1.799 → x1,2080
1.800–1.999 → x1,2370
2.000–2.199 → x1,5500
2.200–2.299 → x1,5900
2.300–2.399 → x1,6300
2.400–2.499 → x1,6700
2.500–2.599 → x1,7285
2.600–2.699 → x1,8000
2.700–2.799 → x1,8800
2.800–2.999 → x2,0000
3.000+ → x1,0000 sui trofei oltre 3.000

Il peso viene applicato per fasce marginali: non moltiplica tutti i trofei del Brawler per il peso della sua fascia finale.

🔥 BONUS E REGOLE OSSERVATE
Sotto 2.000 trofei il battle log può mostrare coppe aggiuntive rispetto al delta base. Il bot misura questo extra come Bonus osservato.

Win Streak: il sistema 2026 può arrivare fino a +10 sotto 2.000 trofei. Il battle log, però, non espone in modo affidabile la causa dell'extra: il bot non etichetta automaticamente un extra come Win Streak se non può dimostrarlo.

Underdog / Sfavorito: può modificare il trophyChange sotto 2.000. Anche in questo caso viene registrato l'extra osservato senza inventarne la causa.

Bot / partite protette: possono avere un'economia diversa da una partita ordinaria; i casi non classificabili restano esplicitamente non classificati.

Da 2.000 trofei: Win Streak, bot e sfavorito sono OFF nel modello 2026.

👥 TEAM VALUE
Il Team Value viene registrato nel tracking delle battaglie per descrivere il livello della squadra e per l'analisi del contesto. Non viene usato come riferimento per pesare la Progressione personale: il peso della Progressione usa i trofei del Brawler del giocatore al momento della battaglia.

🎮 DELTA BASE
Per 3v3/5v5 e altre modalità a squadre, una vittoria ordinaria osservata sotto 2.000 parte da +10; eventuali coppe sopra il delta base vengono separate come Bonus osservato quando classificabili.
Solo, Duo e Trio usano invece le rispettive tabelle di piazzamento verificate.

⚔️ TITANI ABUSIVI
La Progressione non guarda soltanto quante coppe hai guadagnato: tiene conto di dove le hai conquistate, Brawler per Brawler."""


class CommunityFeatures:
    @staticmethod
    def _progression_mode_it(value):
        key = re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
        return PROGRESSION_MODE_NAMES_IT.get(key, "Modalità non riconosciuta")

    @staticmethod
    def _progression_result_it(result, placement=None):
        key = str(result or "").strip().casefold()
        if key in PROGRESSION_RESULT_NAMES_IT:
            return PROGRESSION_RESULT_NAMES_IT[key]
        if placement is not None:
            return f"Posizione {int(placement)}"
        return "Risultato non disponibile"

    @staticmethod
    def _progression_bonus_it(value):
        key = str(value or "").strip().casefold()
        return PROGRESSION_BONUS_NAMES_IT.get(key, "Bonus aggiuntivo")

    def __init__(
        self,
        supabase_url,
        supabase_key,
        player_fetcher,
        history_fetcher,
        change_calculator,
        number_formatter,
        change_formatter,
        snapshot_saver=None,
    ):
        self.supabase_url = (supabase_url or "").rstrip("/")
        self.supabase_key = supabase_key or ""
        self.player_fetcher = player_fetcher
        self.history_fetcher = history_fetcher
        self.change_calculator = change_calculator
        self.number_formatter = number_formatter
        self.change_formatter = change_formatter
        self.snapshot_saver = snapshot_saver

    @property
    def ready(self):
        return bool(self.supabase_url and self.supabase_key)

    def _headers(self, prefer=None):
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _get(self, table, params=None):
        if not self.ready:
            return []
        for attempt in range(3):
            try:
                response = requests.get(
                    f"{self.supabase_url}/rest/v1/{table}",
                    headers=self._headers(),
                    params=params or {},
                    timeout=15,
                )
                response.raise_for_status()
                return response.json()
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                LOG.warning("SUPABASE READ RETRY: table=%s attempt=%s", table, attempt + 1)
                time.sleep(0.5 * (attempt + 1))

    def _post(self, table, payload, params=None, prefer="return=representation"):
        if not self.ready:
            return []
        response = requests.post(
            f"{self.supabase_url}/rest/v1/{table}",
            headers=self._headers(prefer),
            params=params or {},
            json=payload,
            timeout=15,
        )
        if not response.ok:
            print("SUPABASE PATCH ERROR:", table, response.status_code, response.text[:2000], flush=True)
            response.raise_for_status()
        if not response.text:
            return []
        return response.json()

    def _patch(self, table, payload, params=None, prefer="return=minimal"):
        if not self.ready:
            return []
        response = requests.patch(
            f"{self.supabase_url}/rest/v1/{table}",
            headers=self._headers(prefer),
            params=params or {},
            json=payload,
            timeout=15,
        )
        if not response.ok:
            print("SUPABASE PATCH ERROR:", table, response.status_code, response.text[:2000], flush=True)
            response.raise_for_status()
        if not response.text:
            return []
        return response.json()

    def _now_iso(self):
        return datetime.now(timezone.utc).isoformat()

    def _draft_identity(self, raw_name, raw_mode=None):
        """Resolve Italian/English map and mode names to one canonical identity."""
        wanted=str(raw_name or "").strip().casefold()
        wanted_mode=str(raw_mode or "").strip().casefold()
        if not wanted:return None
        # Lazy import avoids the existing live_maps -> community_features startup hook cycle.
        from live_maps import safe_get, localized, brawltrack_pro_map_stats
        names=safe_get("i18n/names.it.json.gz") or {}
        rotation=safe_get("event_rotation.json.gz") or []
        # The analyzer Ranked dataset is the complete map catalog; rotation only
        # contains maps active now and must never limit Draft map recognition.
        ranked_catalog=safe_get("pl-results.json.gz") or {}
        mode_aliases={
            "gem grab":"Gem Grab","gemgrab":"Gem Grab","arraffagemme":"Gem Grab",
            "brawl ball":"Brawl Ball","brawlball":"Brawl Ball","brawl_ball":"Brawl Ball","footbrawl":"Brawl Ball",
            "hot zone":"Hot Zone","hotzone":"Hot Zone","dominio":"Hot Zone","zona rovente":"Hot Zone",
            "bounty":"Bounty","ricercati":"Bounty",
            "heist":"Heist","rapina":"Heist",
            "knockout":"Knockout","k.o.":"Knockout","ko":"Knockout",
        }
        api_modes={
            "gem grab":"gemGrab","brawl ball":"brawlBall","hot zone":"hotZone",
            "bounty":"bounty","heist":"heist","knockout":"knockout",
        }
        # Draft validity must come from the same exact seasonal (mode, map)
        # provider used by the Ranked collector. Never let Trophy rotation maps
        # bypass the current Ranked pool.
        from ranked_matchup_sync import current_ranked_pool
        pool_pairs=current_ranked_pool()
        if not pool_pairs:
            LOG.warning("DRAFT current Ranked pool empty; failing closed")
            return None
        map_names=names.get("maps",{}) if isinstance(names,dict) else {}
        # Localization catalogs are keyed inconsistently across upstream snapshots
        # (some preserve English case, others use uppercase keys). Normalize both
        # directions so Draft always accepts and displays the official Italian name.
        map_it_by_en={str(k).casefold():str(v) for k,v in map_names.items() if v}
        it_to_en={str(v).casefold():str(k) for k,v in map_names.items() if v}
        api_to_en={"gemGrab":"Gem Grab","brawlBall":"Brawl Ball","hotZone":"Hot Zone",
                   "bounty":"Bounty","heist":"Heist","knockout":"Knockout"}
        candidates=[]
        for mode_api,map_en in sorted(pool_pairs):
            candidates.append((str(map_en),api_to_en.get(mode_api,mode_api),str(map_en)))
        for en,raw_event_mode,canonical_key in candidates:
            it=map_it_by_en.get(en.casefold()) or localized(names,"maps",en)
            # localized() is case-sensitive against some cached catalogs; never
            # expose the internal English map name when an Italian mapping exists.
            if str(it).casefold() == en.casefold():
                it = map_it_by_en.get(en.casefold(), it)
            # Also use the reverse localization table directly so a valid
            # Italian map name cannot escape the guided Draft state.
            matched_en=it_to_en.get(wanted)
            if wanted not in {en.casefold(),str(it).casefold()} and not (matched_en and matched_en.casefold()==en.casefold()):continue
            en_mode=mode_aliases.get(raw_event_mode.casefold(), raw_event_mode)
            stats=brawltrack_pro_map_stats(en) or {}
            if not en_mode:
                meta_mode=str(stats.get("mode") or stats.get("game_mode") or "").strip()
                if meta_mode.casefold() in ("brawlball","brawl_ball"):
                    en_mode="Brawl Ball"
                else:
                    en_mode=mode_aliases.get(meta_mode.casefold(),meta_mode)
            it_mode=localized(names,"modes",en_mode) if en_mode else ""
            # Some upstream catalogs expose the API key rather than the English label.
            if str(en_mode).casefold() in ("brawl ball","brawlball","brawl_ball"):
                en_mode="Brawl Ball"
                it_mode="Footbrawl"
            elif str(en_mode).casefold() in ("hot zone","hotzone") and str(it_mode).casefold() in ("hot zone","zona rovente"):
                # Current official Italian client label; localization catalog can lag behind game updates.
                it_mode="Dominio"
            accepted={raw_event_mode.casefold(),str(en_mode).casefold(),str(it_mode).casefold()}-{""}
            accepted.update(k for k,v in mode_aliases.items() if en_mode and v.casefold()==str(en_mode).casefold())
            if wanted_mode and accepted and wanted_mode not in accepted:return None
            mode_api=api_modes.get(str(en_mode).casefold())
            if not mode_api:
                LOG.warning("DRAFT unresolved Ranked API mode for %s: %s",en,en_mode)
                return None
            return {"map_en":en,"map_it":it,"map_id":stats.get("map_id"),"mode_en":en_mode or None,"mode_it":it_mode or None,"mode_api":mode_api,"catalog_key":canonical_key}
        return None

    @staticmethod
    def _ranked_draft_format(rank_name):
        """Current Ranked 2.0 format: all-pick through Gold, ban+all-pick Diamond, turn-pick Mythic+."""
        value=str(rank_name or "").strip().casefold()
        if not value:
            return "unknown"
        if any(x in value for x in ("bronzo","bronze","argento","silver","oro","gold")):
            return "all_pick"
        if any(x in value for x in ("diamante","diamond")):
            return "ban_all_pick"
        if any(x in value for x in ("mito","mitic","mythic","leggenda","legend","maestro","master","campion","pro")):
            return "turn_pick"
        return "unknown"

    @staticmethod
    def _draft_pick_order(first_pick):
        # Official Mythic+ snake: 1-2-2-1. Values are relative to the user's team.
        return ["my","enemy","enemy","my","my","enemy"] if first_pick == "my" else ["enemy","my","my","enemy","enemy","my"]

    def _draft_next_turn_text(self, state):
        if state.get("draft_format") != "turn_pick":
            return None
        first=state.get("first_pick")
        if first not in ("my","enemy"):
            return "Indica chi ha il primo pick: primo pick nostro oppure primo pick avversario."
        index=len(state.get("pick_sequence") or [])
        if index >= 6:
            return "Draft pick completata: 3 nostri e 3 avversari."
        side=self._draft_pick_order(first)[index]
        return f"Pick {index+1}/6: {'NOSTRA SQUADRA' if side == 'my' else 'AVVERSARIO'}."

    def _draft_ranked_map_picks(self, map_name, excluded=None, limit=5):
        """Exact-map Ranked picks: BrawlTrack Pro first, collected Ranked battlelogs second, legacy Ranked dataset last."""
        from live_maps import safe_get, localized, valid_rows, brawltrack_pro_map_stats
        identity=self._draft_identity(map_name)
        if not identity:return []
        names=safe_get("i18n/names.it.json.gz") or {}
        blocked={str(x).strip().casefold() for x in (excluded or []) if x}
        def allowed(raw,label):
            return str(raw or "").strip().casefold() not in blocked and str(label or "").strip().casefold() not in blocked
        # 1) Primary: BrawlTrack exact competitive map evidence.
        pro=brawltrack_pro_map_stats(identity["map_en"]) or {}
        pro_rows=[]
        for row in (pro.get("priority_picks") or []):
            raw=str(row.get("brawler") or "").strip()
            label=localized(names,"brawlers",raw)
            if raw and allowed(raw,label):
                # BrawlTrack exposes both priority rank and exact-map competitive
                # win/use rates. Blend them instead of copying its displayed order:
                # this keeps current meta evidence primary while preferring picks
                # that are actually strong and established on this map.
                try:
                    rank=max(1,int(row.get("rank") or 999))
                    wr=float(row.get("win_rate") or 0)
                    ur=float(row.get("use_rate") or 0)
                except (TypeError,ValueError):
                    continue
                meta_score=wr + min(ur,30.0)*0.18 + max(0.0,16.0-rank)*0.22
                pro_rows.append((meta_score,wr,ur,-rank,str(label)))
        if pro_rows:
            pro_rows.sort(reverse=True)
            return [x[4] for x in pro_rows[:limit]]
        # 2) Fallback: our exact-map Ranked battlelog aggregation in Supabase.
        try:
            rows=self._get("ranked_draft_stats",{
                "select":"brawler_id,games,win_rate",
                "stat_scope":"eq.map_pick","mode":"eq."+identity["mode_api"],
                "map_name":"eq."+identity["map_en"],"order":"games.desc","limit":"100",
            })
            catalog=self._get("brawlers_catalog",{"select":"brawler_id,name_en,name_it"})
            by_id={int(x["brawler_id"]):x for x in (catalog or []) if x.get("brawler_id") is not None}
            ranked=[]
            for row in rows or []:
                b=by_id.get(int(row.get("brawler_id") or 0))
                if not b:continue
                raw=str(b.get("name_en") or "").strip();label=str(b.get("name_it") or raw).strip()
                if not allowed(raw,label):continue
                games=int(row.get("games") or 0);wr=float(row.get("win_rate") or 0)
                ranked.append((games,wr,label))
            if ranked:
                ranked.sort(key=lambda x:(x[0],x[1]),reverse=True)
                return [x[2] for x in ranked[:limit]]
        except Exception as exc:
            LOG.warning("DRAFT Ranked battlelog fallback unavailable map=%s error=%s",identity["map_en"],type(exc).__name__)
        # 3) Last verified fallback: existing Ranked analyzer dataset, never Ladder.
        ranked=safe_get("pl-results.json.gz") or {}
        raw=ranked.get(identity.get("catalog_key"),{}) if isinstance(ranked,dict) else {}
        rows=valid_rows(raw.get("individual")) if isinstance(raw,dict) else []
        eligible=[]
        for row in rows:
            brawler=str(row.get("brawler") or row.get("brawler_name") or "").strip()
            if not brawler:continue
            label=localized(names,"brawlers",brawler)
            if not allowed(brawler,label):continue
            wr=float(row.get("wr",row.get("win_rate",0)) or 0);ur=float(row.get("ur",row.get("use_rate",0)) or 0);sr=float(row.get("sr",row.get("starplayer_rate",0)) or 0)
            if ur < 0.5:continue
            eligible.append((wr+min(ur,25)*0.08+min(sr,30)*0.03,wr,ur,str(label)))
        eligible.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
        return [x[3] for x in eligible[:limit]]

    def _draft_ranked_pick_source(self, map_name):
        """Diagnostic only: identify which verified source supplies exact-map Ranked picks."""
        from live_maps import brawltrack_pro_map_stats
        identity=self._draft_identity(map_name)
        if not identity:return "invalid",0
        pro=brawltrack_pro_map_stats(identity["map_en"]) or {}
        if pro.get("priority_picks"):return "brawltrack",len(pro["priority_picks"])
        try:
            rows=self._get("ranked_draft_stats",{
                "select":"brawler_id,games,win_rate","stat_scope":"eq.map_pick",
                "mode":"eq."+identity["mode_api"],"map_name":"eq."+identity["map_en"],
                "order":"games.desc","limit":"100",
            })
            if rows:return "battlelog",len(rows)
        except Exception:
            pass
        return "legacy",len(self._draft_ranked_map_picks(identity["map_en"],limit=100))

    def draft_map_advice_text(self, map_name, rank_name=None, mode=None):
        """Ranked opener: map-specific Ranked data only."""
        identity=self._draft_identity(map_name,mode)
        if not identity:return None
        pick_names=self._draft_ranked_map_picks(identity["map_en"],limit=6)
        title=f'RANKED - {identity["map_it"].upper()}'
        lines=[title]
        if identity.get("mode_it"):
            lines.append(f'Modalità: {identity["mode_it"]}')
        if rank_name:lines.append(f'Fascia Ranked: {rank_name}')
        if pick_names:lines.append("Migliori pick: "+", ".join(pick_names))
        else:lines.append("La mappa è riconosciuta, ma non ho ancora dati Ranked verificati da mostrare.")
        return "\n".join(lines)

    def draft_comp_advice_text(self, draft_state):
        """Recommend only teammates evidenced by BrawlTrack common final comps."""
        if not draft_state or not draft_state.get("map"):
            return None
        from live_maps import brawltrack_pro_map_stats
        stats=brawltrack_pro_map_stats(draft_state["map"]) or {}
        comps=stats.get("final_comps") or []
        selected=[str(x).strip().casefold() for x in (draft_state.get("my_picks") or []) if x]
        if not selected or not comps:
            return None
        catalog=self._get("brawlers_catalog",{"select":"name_en,name_it"})
        aliases={}
        labels={}
        for row in catalog or []:
            en=str(row.get("name_en") or "").strip()
            it=str(row.get("name_it") or en).strip()
            if en:
                aliases[en.casefold()]=en.casefold()
                labels[en.casefold()]=it
            if it:
                aliases[it.casefold()]=en.casefold()
        chosen={aliases.get(x,x) for x in selected}
        matches=[]
        for comp in comps if isinstance(comps,list) else []:
            team=[str(x).strip().casefold() for x in (comp.get("team") or []) if x] if isinstance(comp,dict) else []
            if chosen and chosen.issubset(set(team)):
                remaining=[labels.get(x,str(x).title()) for x in team if x not in chosen]
                if remaining:
                    matches.append((float(comp.get("win_rate") or 0),int(comp.get("sets") or 0),remaining))
        if not matches:
            return None
        matches.sort(key=lambda x:(x[1],x[0]),reverse=True)
        wr,sets,remaining=matches[0]
        return "Comp compatibile: "+", ".join(remaining)+f" — {sets} set, {wr:g}% WR"

    def brawler_counter_text(self, brawler_name, mode=None, map_name=None):
        """Return verified counter data stored server-side; never invent matchups."""
        try:
            catalog = self._get("brawlers_catalog", {"select": "brawler_id,name_en,name_it"})
            wanted = str(brawler_name or "").strip().casefold()
            target = next((b for b in catalog if wanted in {
                str(b.get("name_en") or "").casefold(), str(b.get("name_it") or "").casefold()
            }), None)
            if not target:
                return None
            params = {
                "select": "counter_brawler_id,mode,map_name,score,sample_size,source,source_updated_at",
                "brawler_id": f"eq.{target['brawler_id']}",
                "order": "score.desc.nullslast",
                "limit": "10",
            }
            if mode:
                params["mode"] = f"eq.{mode}"
            if map_name:
                params["map_name"] = f"eq.{map_name}"
            rows = self._get("brawler_counters", params)
            if not rows:
                return (
                    f"Non ho ancora counter verificati per {target.get('name_it') or target.get('name_en')}. "
                    "Non invento matchup: il dato verrà mostrato quando sarà disponibile nella cache counter."
                )
            names = {int(b["id"]): (b.get("name_it") or b.get("name")) for b in catalog if b.get("id") is not None}
            title = f"COUNTER DI {(target.get('name_it') or target.get('name_en')).upper()}"
            if map_name: title += f" - {map_name}"
            elif mode: title += f" - {mode}"
            lines = [title, ""]
            for i,row in enumerate(rows[:5],1):
                label = names.get(int(row["counter_brawler_id"]), str(row["counter_brawler_id"]))
                detail = f" - indice {float(row['score']):.1f}" if row.get("score") is not None else ""
                lines.append(f"{i}. {label}{detail}")
            return "\n".join(lines)
        except Exception as exc:
            print("ERRORE COUNTER BRAWLER:", repr(exc), flush=True)
            return None

    def get_registered_user(self, telegram_user_id):
        """Resolve an active registered member immediately from Telegram identity."""
        if not self.ready or telegram_user_id is None:
            return None
        try:
            rows=self._get("community_members",{
                "select":"*",
                "telegram_user_id":f"eq.{int(telegram_user_id)}",
                "is_active":"eq.true",
                "player_tag":"not.is.null",
                "order":"player_last_updated_at.desc.nullslast",
                "limit":"1",
            })
            return rows[0] if rows else None
        except Exception as exc:
            print("ERRORE RICONOSCIMENTO UTENTE:",repr(exc),flush=True)
            return None

    @staticmethod
    def _skin_key(value):
        return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

    def _official_owned_skin_ids(self, player_tag):
        """Resolve exact owned skins through the authenticated Netsons BSInfo bridge."""
        global _SKIN_BRIDGE_FAILURES, _SKIN_BRIDGE_OPEN_UNTIL
        import time
        tag=str(player_tag or "").strip().lstrip("#").upper()
        proxy_url=(os.environ.get("BRAWL_OFFICIAL_PROXY_URL") or "").strip()
        proxy_key=(os.environ.get("BRAWL_OFFICIAL_PROXY_KEY") or "").strip()
        if not tag or not proxy_url or not proxy_key:
            raise RuntimeError("Skin collection proxy is not configured")
        now = time.monotonic()
        if now < _SKIN_BRIDGE_OPEN_UNTIL:
            raise SkinBridgeBackoff("Skin collection source temporarily paused")
        try:
            response=requests.get(proxy_url,params={"action":"skincollection","tag":tag},headers={"X-Sens-Key":proxy_key,"Accept":"application/json","User-Agent":"SensGPT-TitaniAbusivi/1.0"},timeout=20)
        except requests.RequestException:
            _SKIN_BRIDGE_FAILURES += 1
            if _SKIN_BRIDGE_FAILURES >= _SKIN_BRIDGE_FAILURE_LIMIT:
                _SKIN_BRIDGE_OPEN_UNTIL = now + _SKIN_BRIDGE_BACKOFF_SECONDS
                LOG.warning("SKINCOLLECTION CIRCUIT OPEN: failures=%s backoff_hours=6", _SKIN_BRIDGE_FAILURES)
            raise
        if not response.ok:
            _SKIN_BRIDGE_FAILURES += 1
            LOG.warning("SKINCOLLECTION BRIDGE UNAVAILABLE: status=%s failures=%s", response.status_code, _SKIN_BRIDGE_FAILURES)
            if _SKIN_BRIDGE_FAILURES >= _SKIN_BRIDGE_FAILURE_LIMIT:
                _SKIN_BRIDGE_OPEN_UNTIL = now + _SKIN_BRIDGE_BACKOFF_SECONDS
                LOG.warning("SKINCOLLECTION CIRCUIT OPEN: failures=%s backoff_hours=6", _SKIN_BRIDGE_FAILURES)
            response.raise_for_status()
        payload=response.json()
        if not isinstance(payload,dict):
            raise RuntimeError("Skin collection payload is not an object")
        names=payload.get("owned_skin_names")
        if not isinstance(names,list) or not names:
            raise RuntimeError("Skin collection payload missing owned skin names")
        catalog=self._get("skins_catalog",{"select":"external_id,name_en,name_it","external_id":"not.is.null","limit":"2000"}) or []
        by_key={}
        for row in catalog:
            try: sid=int(row.get("external_id"))
            except (TypeError,ValueError): continue
            for label in (row.get("name_en"),row.get("name_it")):
                key=self._skin_key(label)
                if key: by_key.setdefault(key,set()).add(sid)
        owned=set()
        ambiguous=0
        unmatched=0
        for name in names:
            ids=by_key.get(self._skin_key(str(name)))
            if ids and len(ids)==1: owned.update(ids)
            elif ids: ambiguous+=1
            else: unmatched+=1
        if not owned:
            raise RuntimeError("Bridge skins could not be mapped to catalog IDs")
        _SKIN_BRIDGE_FAILURES = 0
        _SKIN_BRIDGE_OPEN_UNTIL = 0.0
        try:
            self._post("skin_owned_ids_latest", {
                "player_tag": tag, "owned_skin_ids": sorted(owned),
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }, params={"on_conflict": "player_tag"},
                prefer="resolution=merge-duplicates,return=minimal")
        except Exception as exc:
            LOG.warning("SKIN OWNED IDS CACHE WRITE FAILED: %s", type(exc).__name__)
        print("BSINFO OWNED SKINS:",tag,"names=",len(names),"mapped=",len(owned),"ambiguous=",ambiguous,"unmatched=",unmatched,flush=True)
        return owned

    def _direct_owned_skin_ids(self, player_tag, catalog):
        """Use BSInfo's documented public player collection without the Netsons bridge.

        Only accept a full owned/notOwned partition of the verified catalog.
        Incomplete collections must never turn unknown skins into missing skins.
        """
        global _SKIN_DIRECT_OPEN_UNTIL
        tag = str(player_tag or "").strip().lstrip("#").upper()
        if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", tag):
            raise RuntimeError("Invalid player tag for skin collection")
        if time.monotonic() < _SKIN_DIRECT_OPEN_UNTIL:
            raise SkinBridgeBackoff("Direct skin collection temporarily paused")
        try:
            response = requests.get(
                f"https://api.bsinfox.com/skins/{tag}",
                params={"lang": "en"},
                headers={"Accept": "application/json", "User-Agent": "SensGPT-TitaniAbusivi/1.0"},
                timeout=15,
            )
            if not response.ok:
                LOG.warning("SKIN DIRECT SOURCE UNAVAILABLE: status=%s", response.status_code)
                response.raise_for_status()
            payload = response.json()
            entries = payload.get("brawlers") if isinstance(payload, dict) else None
            if isinstance(entries, dict):
                entries = list(entries.values())
            if not isinstance(entries, list) or not entries:
                raise RuntimeError("Direct source did not return player brawlers")
            owned, missing = set(), set()
            for entry in entries:
                if not isinstance(entry, dict) or not all(isinstance(entry.get(key), list) for key in ("owned", "notOwned")):
                    raise RuntimeError("Direct source lacks ownership flags")
                for key, target in (("owned", owned), ("notOwned", missing)):
                    for skin in entry[key]:
                        if isinstance(skin, dict) and str(skin.get("id") or "").isdigit():
                            target.add(int(skin["id"]))
            catalog_ids = {int(row["external_id"]) for row in catalog
                           if str(row.get("external_id") or "").isdigit()}
            if not catalog_ids or catalog_ids - (owned | missing) or owned & missing:
                raise RuntimeError("Direct collection does not cover verified catalog")
            # The source may include owned IDs outside the verified catalog.
            # Compare Stats against the complete source response, then persist
            # only verified catalog IDs for the per-Brawler split.
            source_owned_count = len(owned)
            owned &= catalog_ids
            saved = self._get("skin_stats_latest", {
                "select": "skins_owned", "player_tag": f"eq.{tag}", "limit": "1",
            }) or []
            if saved and saved[0].get("skins_owned") is not None and source_owned_count != int(saved[0]["skins_owned"]):
                LOG.warning("SKIN DIRECT COUNT MISMATCH: source=%s catalog=%s stats=%s",
                            source_owned_count, len(owned), saved[0]["skins_owned"])
                previous = self._get("skin_owned_ids_latest", {
                    "select": "owned_skin_ids", "player_tag": f"eq.{tag}", "limit": "1",
                }) or []
                prior_ids = previous[0].get("owned_skin_ids") if previous else []
                confirmed = owned | {int(sid) for sid in prior_ids if str(sid).isdigit()} if isinstance(prior_ids, list) else owned
                # Retain positive evidence across snapshots. Incomplete
                # source results never establish that another skin is absent.
                if len(confirmed) <= int(saved[0]["skins_owned"]) and len(confirmed) > len(prior_ids or []):
                    self._post("skin_owned_ids_latest", {
                        "player_tag": tag, "owned_skin_ids": sorted(confirmed),
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }, params={"on_conflict": "player_tag"}, prefer="resolution=merge-duplicates,return=minimal")
                raise RuntimeError("Direct collection differs from Stats count")
            self._post("skin_owned_ids_latest", {
                "player_tag": tag, "owned_skin_ids": sorted(owned),
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }, params={"on_conflict": "player_tag"}, prefer="resolution=merge-duplicates,return=minimal")
            _SKIN_DIRECT_OPEN_UNTIL = 0.0
            LOG.info("SKIN DIRECT COLLECTION VERIFIED: source=%s owned_in_catalog=%s catalog=%s",
                     source_owned_count, len(owned), len(catalog_ids))
            return owned
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            # Avoid repeated expensive requests if the public API is unavailable.
            _SKIN_DIRECT_OPEN_UNTIL = time.monotonic() + 30 * 60
            LOG.warning("SKIN DIRECT SOURCE FAILED: reason=%s", type(exc).__name__)
            raise

    @staticmethod
    def _skin_category_label(row):
        """Italian display category without inventing a rarity missing from game data."""
        rarity = str(row.get("rarity") or "").upper()
        tid = str((row.get("source_payload") or {}).get("tid") or "").upper()
        conf = str((row.get("source_payload") or {}).get("conf") or "").upper()
        # True Silver/Gold: use verified catalog metadata, not generic names ending in Gold/Silver.
        acquisition_type = str(row.get("acquisition_type") or "").lower()
        acquisition_note = str(row.get("acquisition_note") or "")
        if "TRUE_GOLD" in tid or (acquisition_type == "coins" and int(row.get("price_coins") or 0) == 25000):
            return "Oro"
        if "TRUE_SILVER" in tid or (acquisition_type == "coins" and int(row.get("price_coins") or 0) == 10000):
            return "Argento"
        if "Skin Overdrive Pass Pro" in acquisition_note:
            return "Skin Overdrive Pass Pro"
        if acquisition_type == "brawl_pass" or is_verified_brawl_pass_skin(row):
            return "Brawl Pass"
        labels = {
            "RARE": "Rare", "SUPER_RARE": "Super rare", "EPIC": "Epiche",
            "MYTHIC": "Mitiche", "LEGENDARY": "Leggendarie",
            "HYPERCHARGE": "Skin Overdrive", "COLLECTORS": "Collezione",
            "RANKED_PASS": "Pass Pro",
        }
        if rarity:
            return labels.get(rarity, rarity.replace("_", " ").title())
        if "PROPASS_PROGRESSION" in tid:
            return "Pass Pro"
        if tid in ("TID_UNDERTAKER_HAT_SKIN", "TID_ROCKET_GIRL_ORIGINAL"):
            return "Base (varianti)"
        return "Senza rarità"

    @staticmethod
    def _skin_category_labels(row):
        """A Pass skin also retains its real rarity in the Stats denominator."""
        primary = CommunityFeatures._skin_category_label(row)
        rarity = str(row.get("rarity") or "").upper()
        rarity_labels = {
            "RARE": "Rare", "SUPER_RARE": "Super rare", "EPIC": "Epiche",
            "MYTHIC": "Mitiche", "LEGENDARY": "Leggendarie",
            "HYPERCHARGE": "Skin Overdrive",
        }
        secondary = rarity_labels.get(rarity) if primary == "Brawl Pass" else None
        return (primary, secondary) if secondary else (primary,)

    def _resolve_skin_brawler_name(self, value):
        wanted = self._skin_key(value)
        try:
            catalog = self._get("brawlers_catalog", {"select": "brawler_id,name_en,name_it"})
            for row in catalog or []:
                if wanted in (self._skin_key(row.get("name_en")), self._skin_key(row.get("name_it"))):
                    brawler_id = row.get("brawler_id")
                    if brawler_id is not None:
                        skin_rows = self._get("skins_catalog", {
                            "select": "brawler_name",
                            "brawler_id": f"eq.{int(brawler_id)}",
                            "verification_status": "eq.structured_verified",
                            "limit": "1",
                        })
                        if skin_rows and skin_rows[0].get("brawler_name"):
                            return str(skin_rows[0]["brawler_name"])
                    return str(row.get("name_en") or row.get("name_it") or value)
        except Exception as exc:
            print("ERRORE RISOLUZIONE BRAWLER SKIN:", repr(exc), flush=True)
        return str(value or "").strip()

    def _brawlvalue_skin_image(self, brawler_name, skin_name):
        try:
            from bs4 import BeautifulSoup
            slug = re.sub(r"[^a-z0-9]+", "-", str(brawler_name or "").casefold()).strip("-")
            if not slug:
                return None
            response = requests.get(f"https://brawlvalue.com/en/skins/{slug}", timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            wanted = self._skin_key(skin_name)
            for img in soup.find_all("img"):
                alt = str(img.get("alt") or "")
                alt_key = self._skin_key(re.sub(r"\\s*-\\s*[^-]*skin\\s*$", "", alt, flags=re.I))
                if alt_key != wanted:
                    continue
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://brawlvalue.com" + src
                if src.startswith(("https://", "http://")):
                    return src
        except Exception as exc:
            print("ERRORE IMMAGINE BRAWL VALUE:", repr(exc), flush=True)
        return None

    async def send_skin_image(self, message, brawler_name, skin_name):
        try:
            canonical_brawler = self._resolve_skin_brawler_name(brawler_name)
            rows = self._get("skins_catalog", {
                "select": "external_id,name_en,name_it,brawler_name,image_verified",
                "verification_status": "eq.structured_verified",
                "brawler_name": f"eq.{canonical_brawler}",
                "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
            })
            wanted = self._skin_key(skin_name)
            matches = [r for r in rows or [] if wanted in (self._skin_key(r.get("name_it")), self._skin_key(r.get("name_en")))]
            if len(matches) != 1:
                return "Non trovo una corrispondenza univoca per questa skin."
            row = matches[0]
            if row.get("image_verified") is not True:
                return "La skin è nel catalogo, ma la sua immagine non è ancora verificata."
            image_url = self._brawlvalue_skin_image(canonical_brawler, row.get("name_en") or row.get("name_it"))
            if not image_url:
                return "Ho trovato la skin nel catalogo, ma Brawl Value non mi ha restituito un'immagine verificabile."
            image = requests.get(image_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            image.raise_for_status()
            if not str(image.headers.get("Content-Type") or "").lower().startswith("image/"):
                return "L'immagine trovata non è valida."
            if len(image.content) > 10 * 1024 * 1024:
                return "L'immagine della skin è troppo grande per essere inviata."
            await message.reply_photo(photo=image.content, caption=str(row.get("name_it") or row.get("name_en") or skin_name))
            return True
        except Exception as exc:
            print("ERRORE INVIO SKIN IMAGE:", repr(exc), flush=True)
            return "Non riesco a recuperare l'immagine della skin in questo momento."

    def _owned_skin_stats_text(self, player_tag):
        """Combine Stats ownership with current verified catalog denominators."""
        from player_tracking import _brawlytix_progression
        stats = _brawlytix_progression(player_tag) or {}
        owned = stats.get("skins_owned")
        raw_counts = stats.get("skin_rarity_counts") or {}
        snapshot_note = None
        if owned is None and not raw_counts:
            tag = str(player_tag or "").strip().lstrip("#").upper()
            cached = self._get("skin_stats_latest", {
                "select": "skins_owned,skin_rarity_counts,observed_at",
                "player_tag": f"eq.{tag}", "limit": "1",
            }) or []
            if not cached:
                return "Stats non risponde e non è ancora disponibile un conteggio Stats salvato per questo account. Riprova più tardi."
            owned = cached[0].get("skins_owned")
            raw_counts = cached[0].get("skin_rarity_counts") or {}
            observed = str(cached[0].get("observed_at") or "")
            snapshot_note = f"📅 Ultimo Stats salvato: {observed[:16].replace('T', ' ')} UTC (non aggiornato)"
        catalog, offset = [], 0
        while True:
            page = self._get("skins_catalog", {
                "select": "external_id,name_en,rarity,source_payload,price_coins,acquisition_type,acquisition_note",
                "verification_status": "eq.structured_verified",
                "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
                "order": "external_id.asc", "limit": "1000", "offset": str(offset),
            }) or []
            catalog.extend(page)
            if len(page) < 1000:
                break
            offset += 1000
        totals = {}
        for row in catalog:
            for label in self._skin_category_labels(row):
                totals[label] = totals.get(label, 0) + 1
        labels = (
            ("rare", "🟢", "Rare", "Rare"), ("super rare", "🔵", "Super rare", "Super rare"),
            ("epic", "🟣", "Epiche", "Epiche"), ("mythic", "🔴", "Mitiche", "Mitiche"),
            ("legendary", "🟡", "Leggendarie", "Leggendarie"),
            ("hypercharge", "🔥", "Skin Overdrive", "Skin Overdrive"),
            ("ranked", "🏅", "Pass Pro", "Pass Pro"),
            ("true silver", "🥈", "Argento", "Argento"),
            ("true gold", "🥇", "Oro", "Oro"),
        )
        counts = {str(key).strip().casefold(): int(value) for key, value in raw_counts.items()
                  if value is not None and str(value).strip().isdigit()}
        total_display = f"{int(owned) if owned is not None else 'n.d.'}/{len(catalog) if catalog else 'n.d.'}"
        lines = ["SKIN POSSEDUTE — ACCOUNT", "", f"🎨 Totale rilevato da Stats: {total_display}"]
        # The Stats total and the named collection have different coverage.
        # Report both without inventing IDs for the difference or lowering the
        # user's total when the collection source is incomplete.
        try:
            tag = str(player_tag or "").strip().lstrip("#").upper()
            snapshots = self._get("skin_owned_ids_latest", {
                "select": "owned_skin_ids,observed_at", "player_tag": f"eq.{tag}", "limit": "1",
            }) or []
            ids = snapshots[0].get("owned_skin_ids") if snapshots else None
            if isinstance(ids, list) and owned is not None:
                catalog_ids = {str(row.get("external_id")) for row in catalog}
                identified = len({str(sid) for sid in ids if str(sid) in catalog_ids})
                if 0 < identified <= int(owned):
                    lines.extend(["", f"✅ Identificate per nome nel catalogo: {identified}"])
                    if int(owned) > identified:
                        lines.append(f"❔ Senza ID verificato: {int(owned) - identified}")
                    observed = str(snapshots[0].get("observed_at") or "")
                    if observed:
                        lines.append(f"📅 Collezione identificata: {observed[:16].replace('T', ' ')} UTC")
        except (TypeError, ValueError, requests.RequestException) as exc:
            LOG.warning("SKIN IDENTIFIED COUNT UNAVAILABLE: %s", type(exc).__name__)
        if snapshot_note:
            lines.extend(["", snapshot_note])
        lines.extend(["", "📊 PER RARITÀ"])
        for key, emoji, name, catalog_label in labels:
            if key in counts or catalog_label in totals:
                numerator = str(counts[key]) if key in counts else "n.d."
                denominator = str(totals[catalog_label]) if catalog_label in totals else "n.d."
                lines.extend(["", f"{emoji} {name}", f"{numerator}/{denominator}"])
        # Stats reports rarity aggregates only: it does not identify ownership
        # of individual base or Brawl Pass skins. Preserve unknown numerators.
        for name, emoji in (("Brawl Pass", "🎟️"), ("Base (varianti)", "🎮")):
            if name in totals:
                lines.extend(["", f"{emoji} {name}", f"n.d./{totals[name]}"])
                if name == "Brawl Pass":
                    lines.append("Provenienza verificata: queste skin compaiono anche nella rispettiva rarità. Stagioni precedenti in verifica.")
        # The 1131 catalog entries already define the global denominator.
        # Brawler defaults are not catalog skin IDs; counting them here would
        # inflate the total and misrepresent ownership in Stats.
        known = {key for key, _, _, _ in labels}
        for key in sorted(counts.keys() - known):
            lines.extend(["", f"🎨 {key.replace('_', ' ').title()}", f"{counts[key]}/n.d."])
        if not counts:
            lines.extend(["", "La suddivisione delle skin possedute non è disponibile."])
        elif owned is not None and sum(counts.values()) < int(owned):
            lines.extend(["", f"Altre categorie non suddivise: {int(owned) - sum(counts.values())} skin possedute"])
        return "\n".join(lines)

    def _cached_skin_account_text(self, player_tag, detailed=False):
        """Exact collection unavailable: use the Stats account ownership data."""
        return self._owned_skin_stats_text(player_tag)

    def _skin_category_telegraph_urls(self):
        """Publish public catalog category indexes once per catalog revision.

        These pages contain catalog entries, never unverified account ownership.
        A failed Telegraph page is retried on the next request.
        """
        catalog, offset = [], 0
        while True:
            page = self._get("skins_catalog", {
                "select": "external_id,brawler_id,brawler_name,name_en,name_it,rarity,source_payload,price_gems,price_coins,acquisition_type,acquisition_note,image_url,image_verified",
                "verification_status": "eq.structured_verified",
                "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
                "order": "external_id.asc", "limit": "1000", "offset": str(offset),
            }) or []
            catalog.extend(page)
            if len(page) < 1000:
                break
            offset += 1000
        if not catalog:
            return {}
        fingerprint = hashlib.sha256(repr(sorted((
            str(r.get("external_id")), str(r.get("brawler_id")), str(r.get("name_it")),
            str(r.get("rarity")), str(r.get("price_gems")), str(r.get("price_coins")),
            str(r.get("acquisition_type")), str(r.get("acquisition_note")),
            repr(r.get("source_payload")),
        ) for r in catalog)).encode("utf-8")).hexdigest()
        with _SKIN_CATEGORY_URLS_LOCK:
            urls = _SKIN_CATEGORY_URLS.setdefault(fingerprint, {})
            categories = {}
            for row in catalog:
                for label in self._skin_category_labels(row):
                    categories.setdefault(label, []).append(row)
            brawlers = self._get("brawlers_catalog", {"select": "brawler_id,name_it,name_en"}) or []
            names = {str(row.get("brawler_id")): str(row.get("name_it") or row.get("name_en") or "")
                     for row in brawlers}
            for category, rows in sorted(categories.items()):
                if category in urls:
                    continue
                grouped = {}
                for row in rows:
                    bid = str(row.get("brawler_id") or "")
                    name = names.get(bid) or str(row.get("brawler_name") or "Brawler")
                    grouped.setdefault(name, []).append(row)
                lines = [f"SKIN — {category.upper()}", "", f"🎨 Skin nel catalogo: {len(rows)}",
                         f"🦸 Brawler: {len(grouped)}", "",
                         "I totali posseduti del tuo account sono nel riepilogo Skin."]
                if category == "Brawl Pass":
                    lines.extend(["", "🎟️ Provenienza verificata: ogni skin compare anche nella sua rarità. Stagioni più vecchie in verifica."])
                lines.extend(["", "🧭 SCEGLI UN BRAWLER"])
                for name, skins in sorted(grouped.items(), key=lambda pair: pair[0].casefold()):
                    heading = name.upper()
                    fragment = quote(heading.replace(" ", "-"), safe="-")
                    lines.append(f"[[SKINJUMP:{fragment}|🦸 {heading} · {len(skins)} skin]]")
                for name, skins in sorted(grouped.items(), key=lambda pair: pair[0].casefold()):
                    lines.extend(["", f"[[SKINBRAWLER:{name.upper()}]]", f"🎨 {len(skins)} skin"])
                    for skin in sorted(skins, key=lambda row: str(row.get("name_it") or row.get("name_en") or "").casefold()):
                        label = str(skin.get("name_it") or skin.get("name_en") or "Skin")
                        gems = skin.get("price_gems")
                        price = f" · 💎 {gems} gemme" if skin.get("acquisition_type") == "gems" and gems is not None else ""
                        photo = str(skin.get("image_url") or "")
                        if skin.get("image_verified") is True and re.fullmatch(
                            r"https://cdn\.bsinfox\.com/brawlers/skins/\d+\.(?:webp|png)", photo
                        ):
                            lines.append(f"[[SKINPHOTO:{photo}|🎨 {label}{price} · 📷 Foto]]")
                        else:
                            lines.append(f"🎨 {label}{price}")
                url = self._publish_telegraph(f"Skin {category}", lines)
                if url:
                    urls[category] = url
            # Keep one public entry point for the verified catalog. Each
            # category page already groups its entries by Brawler and links to
            # the individual skin photo, including across Telegraph splits.
            if "Catalogo" not in urls and all(category in urls for category in categories):
                lines = ["SKIN — CATALOGO", "", f"🎨 Skin verificate: {len(catalog)}",
                         "", "🧭 SCEGLI UNA CATEGORIA"]
                for category in sorted(categories, key=lambda label: (
                        {"Rare": 10, "Super rare": 20, "Epiche": 30,
                         "Mitiche": 40, "Leggendarie": 50,
                         "Skin Overdrive": 60, "Brawl Pass": 70,
                         "Pass Pro": 80, "Argento": 90, "Oro": 100}.get(label, 110), label)):
                    lines.append(f"[[SKINLINK:{urls[category]}|🎨 {category} · {len(categories[category])} skin]]")
                lines.extend(["", "Scegli una categoria e poi un Brawler per vedere nomi, foto e prezzi verificati."])
                catalog_url = self._publish_telegraph("Skin Catalogo", lines)
                if catalog_url:
                    urls["Catalogo"] = catalog_url
            return dict(urls)

    async def send_skin_telegraph(self, context, chat_id, answer):
        """Read Skin output on Telegraph; keep Telegram limited to the access button."""
        if not isinstance(answer, str) or "\n" not in answer:
            await self._send_ranking_message(context, chat_id, answer)
            return
        title = answer.splitlines()[0]
        lines = answer.splitlines()
        if title == "SKIN POSSEDUTE — ACCOUNT":
            try:
                urls = await asyncio.to_thread(self._skin_category_telegraph_urls)
                if urls.get("Catalogo"):
                    lines.extend(["", f"[[SKINLINK:{urls['Catalogo']}|🗂️ Catalogo completo per rarità e Brawler]]"])
                lines = [f"[[SKINLINK:{urls[label]}|{line}]]" if label in urls else line
                         for line in lines
                         for label in [re.sub(r"^[^\w]+\s*", "", line).strip()]]
            except Exception as exc:
                LOG.warning("SKIN CATEGORY INDEX UNAVAILABLE: reason=%s", type(exc).__name__)
        url = await asyncio.to_thread(self._publish_telegraph, title, lines)
        if url:
            await self._send_ranking_message(context, chat_id, self._telegraph_reply(
                [title, "Apri la pagina per leggere i dati Skin."], url,
                ["La pagina Skin non è disponibile in questo momento."],
            ))
        else:
            await self._send_ranking_message(context, chat_id, "La pagina Telegraph Skin non è disponibile in questo momento. Riprova più tardi.")

    def skin_account_text(self, registered_user, brawler_name=None, rarity=None, category=None, mode="summary"):
        if not registered_user or not registered_user.get("player_tag"):
            return "Devi prima registrare il tuo tag Brawl Stars."
        try:
            if not brawler_name and not rarity and not category and mode == "summary":
                return self._owned_skin_stats_text(registered_user["player_tag"])
            catalog, offset = [], 0
            while True:
                page = self._get("skins_catalog", {
                    "select": "external_id,brawler_id,name_en,name_it,rarity,brawler_name,source_payload,price_gems,price_coins,acquisition_type,availability_status,acquisition_note,acquisition_group_key,acquisition_group_type,acquisition_group_cost_eur",
                    "verification_status": "eq.structured_verified",
                    "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
                    "order": "brawler_name.asc,name_en.asc", "limit": "1000", "offset": str(offset),
                })
                catalog.extend(page)
                if len(page) < 1000: break
                offset += 1000
            if not catalog:
                return "Il catalogo skin non è disponibile in questo momento."
            rows = list(catalog)
            if brawler_name:
                canonical = self._resolve_skin_brawler_name(brawler_name)
                wanted = self._skin_key(canonical)
                rows = [r for r in rows if self._skin_key(r.get("brawler_name")) == wanted]
                if not rows: return f"Non trovo il Brawler {brawler_name} nel catalogo skin."
            if rarity:
                wanted = self._skin_key(rarity)
                rows = [r for r in rows if self._skin_key(r.get("rarity")) == wanted]
            if category:
                wanted = self._skin_key(category)
                rows = [r for r in rows if any(self._skin_key(label) == wanted
                                                for label in self._skin_category_labels(r))]
            if not rows:
                return f"Non risultano skin in questa categoria{' per '+brawler_name if brawler_name else ''}."
            ownership_note = None
            ownership_complete = True
            try:
                owned_ids = self._official_owned_skin_ids(registered_user["player_tag"])
            except (SkinBridgeBackoff, requests.RequestException, RuntimeError):
                tag = str(registered_user["player_tag"]).strip().lstrip("#").upper()
                try:
                    owned_ids = self._direct_owned_skin_ids(tag, catalog)
                except (SkinBridgeBackoff, requests.RequestException, RuntimeError):
                    owned_ids = None
                if owned_ids is None:
                    saved = self._get("skin_owned_ids_latest", {
                        "select": "owned_skin_ids,observed_at", "player_tag": f"eq.{tag}", "limit": "1",
                    }) or []
                    if not saved or not isinstance(saved[0].get("owned_skin_ids"), list) or not saved[0]["owned_skin_ids"]:
                        raise
                    owned_ids = {int(sid) for sid in saved[0]["owned_skin_ids"] if str(sid).isdigit()}
                    observed = str(saved[0].get("observed_at") or "")
                    ownership_note = f"📅 Ultima collezione verificata: {observed[:16].replace('T', ' ')} UTC (non aggiornata)"
                    # An observed subset is proof of ownership for its IDs,
                    # never proof that all other catalog entries are missing.
                    stats = self._get("skin_stats_latest", {
                        "select": "skins_owned", "player_tag": f"eq.{tag}", "limit": "1",
                    }) or []
                    reported = stats[0].get("skins_owned") if stats else None
                    ownership_complete = reported is not None and len(owned_ids) == int(reported)
                    if not ownership_complete:
                        ownership_note += " · collezione parziale: le altre skin non sono classificabili come mancanti"
            for r in rows:
                r["_owned"] = r.get("external_id") is not None and int(r["external_id"]) in owned_ids
            owned = [r for r in rows if r["_owned"]]
            missing = [r for r in rows if not r["_owned"]] if ownership_complete else []
            unknown = [r for r in rows if not r["_owned"]] if not ownership_complete else []
            skin_name = lambda r: str(r.get("name_it") or r.get("name_en") or "")
            title = None
            if brawler_name:
                bid = rows[0].get("brawler_id")
                br = self._get("brawlers_catalog", {"select":"brawler_id,name_en,name_it","brawler_id":f"eq.{int(bid)}","limit":"1"}) if bid is not None else []
                title = str((br[0].get("name_it") if br else None) or brawler_name).upper()

            order = {"Rare":10,"Super rare":20,"Epiche":30,"Mitiche":40,"Leggendarie":50,"Skin Overdrive":60,"Skin Overdrive Pass Pro":65,"Pass Pro":70,"Brawl Pass":80,"Collezione":90,"Senza rarità":100,"Argento":1000,"Oro":1001}
            def add_values(lines, selected, label):
                gems = sum(int(r.get("price_gems") or 0) for r in selected if r.get("acquisition_type") == "gems")
                coins = sum(int(r.get("price_coins") or 0) for r in selected if r.get("acquisition_type") == "coins")
                paid_groups = {}
                for r in selected:
                    group_key = str(r.get("acquisition_group_key") or "").strip()
                    group_cost = r.get("acquisition_group_cost_eur")
                    if group_key and group_cost is not None:
                        try: paid_groups[group_key] = float(group_cost)
                        except (TypeError, ValueError): pass
                euro = sum(paid_groups.values())
                special = sum(1 for r in selected if r.get("acquisition_type") not in ("gems","coins") and not r.get("acquisition_group_key"))
                lines += ["", f"VALORE SKIN {label}", f"💎 Gemme: {gems:,}".replace(",","."), f"🪙 Monete: {coins:,}".replace(",",".")]
                if euro:
                    lines.append(f"💶 Pass/set: {euro:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
                if special: lines.append(f"🎟️ Senza prezzo diretto verificato: {special}")

            if mode in ("owned","missing"):
                if mode == "missing" and not ownership_complete:
                    return "Non posso stabilire quali skin ti mancano: la collezione salvata è parziale. " + ownership_note
                selected = owned if mode == "owned" else missing
                label = ("POSSEDUTE IDENTIFICATE" if not ownership_complete else "POSSEDUTE") if mode == "owned" else "MANCANTI"
                heading = title if brawler_name else (category or rarity or "SKIN").upper()
                lines = [f"{heading} — SKIN {label} ({len(selected)}/{len(rows)})"]
                if selected:
                    if brawler_name and not category and not rarity:
                        groups = {}
                        for r in selected: groups.setdefault(self._skin_category_label(r), []).append(r)
                        for key, group in sorted(groups.items(), key=lambda x:(order.get(x[0],500),x[0])):
                            lines += ["", key.upper(), ", ".join(skin_name(r) for r in group)]
                    else:
                        lines += ["", ", ".join(skin_name(r) for r in selected)]
                else:
                    lines += ["", "Nessuna." if mode == "owned" else "Nessuna: le possiedi tutte."]
                add_values(lines, selected, label)
                if ownership_note: lines.insert(1, ownership_note)
                return "\n".join(lines)

            if not brawler_name and (rarity or category):
                label = category or rarity.title()
                lines = [f"SKIN ACCOUNT — {label}", f"{'Possedute identificate' if not ownership_complete else 'Possedute'}: {len(owned)}/{len(rows)}", "", "NOMI POSSEDUTI IDENTIFICATI", ", ".join(skin_name(r) for r in owned) if owned else "Nessuna."]
                if ownership_complete:
                    lines.extend(["", f"Mancanti: {len(missing)}", "NOMI MANCANTI", ", ".join(skin_name(r) for r in missing) if missing else "Nessuna: le possiedi tutte."])
                else:
                    lines.extend(["", f"Da verificare: {len(unknown)} · non sono necessariamente mancanti"])
                add_values(lines, owned, "POSSEDUTE IDENTIFICATE" if not ownership_complete else "POSSEDUTE")
                if ownership_complete: add_values(lines, missing, "MANCANTI")
                if ownership_note: lines.insert(1, ownership_note)
                return "\n".join(lines)

            if not brawler_name:
                if not ownership_complete:
                    return "La collezione salvata identifica %s skin nel catalogo, ma non permette di distinguere tutte le altre. %s" % (len(owned), ownership_note)
                groups = {}
                for r in rows:
                    groups.setdefault(self._skin_category_label(r), []).append(r)
                lines=[f"SKIN ACCOUNT\nTotale: {len(owned)}/{len(rows)}", f"Mancanti: {len(missing)}", f"Completamento: {len(owned)*100.0/len(rows):.1f}%", "", "PER RARITÀ"]
                for key, group in sorted(groups.items(), key=lambda x:(order.get(x[0],500),x[0])):
                    if key in ("Argento", "Oro"):
                        brawlers = {r["brawler_id"] for r in group if r.get("brawler_id") is not None}
                        owned_brawlers = {r["brawler_id"] for r in group if r.get("brawler_id") is not None and r["_owned"]}
                        lines.append(f"{key}: {len(owned_brawlers)}/{len(brawlers)} Brawler ({len(group)} varianti)")
                    else:
                        lines.append(f"{key}: {sum(1 for r in group if r['_owned'])}/{len(group)}")

                brawler_names = {}
                try:
                    for br in self._get("brawlers_catalog", {"select":"brawler_id,name_en,name_it"}) or []:
                        if br.get("brawler_id") is not None:
                            brawler_names[int(br["brawler_id"])] = str(br.get("name_it") or br.get("name_en") or br["brawler_id"])
                except Exception as exc:
                    print("ERRORE NOMI BRAWLER SKIN:", repr(exc), flush=True)
                per_brawler = {}
                for r in rows:
                    bid = r.get("brawler_id")
                    key = int(bid) if bid is not None else self._skin_key(r.get("brawler_name"))
                    d = per_brawler.setdefault(key, [0, 0, str(r.get("brawler_name") or key)])
                    d[1] += 1
                    if r["_owned"]: d[0] += 1
                lines += ["", "PER BRAWLER"]
                display_rows = []
                for key,(have,total,fallback) in per_brawler.items():
                    name = brawler_names.get(key, fallback) if isinstance(key, int) else fallback
                    display_rows.append((self._skin_key(name), name, have, total))
                for _,name,have,total in sorted(display_rows):
                    pct = have * 100.0 / total if total else 0.0
                    lines.append(f"{name}: {have}/{total} — {pct:.1f}%")
                add_values(lines, owned, "POSSEDUTE"); add_values(lines, missing, "MANCANTI")
                if ownership_note: lines.insert(1, ownership_note)
                return "\n".join(lines)

            groups={}
            for r in rows: groups.setdefault(self._skin_category_label(r),[]).append(r)
            lines=[f"{title} — SKIN", f"🎨 {'Identificate' if not ownership_complete else 'Totale'}: {len(owned)}/{len(rows)}", "", "📊 PER CATEGORIA"]
            for key,group in sorted(groups.items(), key=lambda x:(order.get(x[0],500),x[0])):
                have = [skin_name(r) for r in group if r["_owned"]]
                absent = [skin_name(r) for r in group if not r["_owned"]]
                lines.extend(["", f"🎨 {key} — {len(have)}/{len(group)}",
                              "✅ Possedute: " + (", ".join(have) if have else "Nessuna"),
                              ("❔ Da verificare: " if not ownership_complete else "❌ Mancanti: ") + (", ".join(absent) if absent else "Nessuna")])
            if ownership_note: lines.insert(1, ownership_note)
            return "\n".join(lines)
        except Exception as exc:
            print("ERRORE SKIN ACCOUNT:", repr(exc), flush=True)
            if brawler_name or rarity or category or mode != "summary":
                return "La fonte dei nomi delle Skin possedute non è disponibile in questo momento. Non posso distinguere le Skin possedute dalle mancanti: riprova più tardi."
            if isinstance(exc, (SkinBridgeBackoff, requests.RequestException)):
                try:
                    cached = self._cached_skin_account_text(
                        registered_user["player_tag"],
                        detailed=bool(brawler_name or rarity or category or mode != "summary"),
                    )
                    if cached:
                        return cached
                except Exception as cache_exc:
                    LOG.warning("SKIN SNAPSHOT FALLBACK ERROR: %s", type(cache_exc).__name__)
                return "La fonte Skin Collection è temporaneamente non disponibile e non ho una rilevazione salvata per il tuo account. Riprova più tardi."
            return "Non riesco a leggere la tua Skin Collection in questo momento."

    def is_registered_private_user(self, telegram_user_id):
        """Private bot access is reserved to active registered community members."""
        return self.get_registered_user(telegram_user_id) is not None

    def track_activity(self, message):
        if not self.ready or not message or not message.from_user:
            return
        if getattr(message.chat, "type", None) not in ("group", "supergroup"):
            return
        user = message.from_user
        payload = {
            "chat_id": int(message.chat_id),
            "telegram_user_id": int(user.id),
            "telegram_username": user.username,
            "display_name": user.full_name,
            "last_seen_at": self._now_iso(),
            "is_active": True,
        }
        try:
            self._post(
                "community_members",
                payload,
                params={"on_conflict": "chat_id,telegram_user_id"},
                prefer="resolution=merge-duplicates,return=minimal",
            )
            self._post(
                "community_settings",
                {"chat_id": int(message.chat_id)},
                params={"on_conflict": "chat_id"},
                prefer="resolution=merge-duplicates,return=minimal",
            )
        except Exception as exc:
            print("ERRORE TRACK ATTIVITA:", repr(exc), flush=True)

    def members(self, chat_id, active_only=True):
        params = {
            "select": "*",
            "chat_id": f"eq.{int(chat_id)}",
            "order": "display_name.asc",
        }
        if active_only:
            params["is_active"] = "eq.true"
        try:
            return self._get("community_members", params)
        except Exception as exc:
            print("ERRORE LETTURA MEMBRI:", repr(exc), flush=True)
            return []

    def register_member(self, message, player_tag):
        user = message.from_user
        requested_tag=str(player_tag or "").upper().replace("#","").strip()
        # Registration is permanent for members: re-running registrami may refresh
        # the same account, but it must never replace it with another player tag.
        existing=self.get_registered_user(user.id)
        if existing:
            current_tag=str(existing.get("player_tag") or "").upper().replace("#","").strip()
            if current_tag and current_tag != requested_tag:
                return {
                    "_registration_locked": True,
                    "tag": "#"+current_tag,
                    "name": existing.get("player_name") or existing.get("display_name") or "Account registrato",
                }
        player = self.player_fetcher(requested_tag)
        if not player:
            return None
        payload = {
            "chat_id": int(message.chat_id),
            "telegram_user_id": int(user.id),
            "telegram_username": user.username,
            "display_name": user.full_name,
            "player_tag": player["tag"].replace("#", ""),
            "player_name": player["name"],
            "ranked_current": player.get("ranked_current"),
            "ranked_peak": player.get("ranked_peak"),
            "ranked_current_elo": player.get("ranked_current_elo"),
            "ranked_season_peak": player.get("ranked_season_peak"),
            "ranked_season_peak_elo": player.get("ranked_season_peak_elo"),
            "ranked_career_peak": player.get("ranked_career_peak"),
            "ranked_career_peak_elo": player.get("ranked_career_peak_elo"),
            "player_last_updated_at": self._now_iso(),
            "last_seen_at": self._now_iso(),
            "is_active": True,
        }
        self._post(
            "community_members",
            payload,
            params={"on_conflict": "chat_id,telegram_user_id"},
            prefer="resolution=merge-duplicates,return=minimal",
        )
        if self.snapshot_saver and player.get("trophies") is not None:
            try:
                self.snapshot_saver(player["tag"], player["name"], player["trophies"])
            except Exception as exc:
                print("ERRORE SNAPSHOT REGISTRAZIONE:", repr(exc), flush=True)
        return player

    def additional_accounts(self, telegram_user_id):
        try:
            return self._get("community_member_accounts",{"select":"*","telegram_user_id":f"eq.{int(telegram_user_id)}","is_active":"eq.true","order":"account_order.asc"}) or []
        except Exception as exc:
            print("ERRORE ACCOUNT AGGIUNTIVI:",repr(exc),flush=True)
            return []

    def add_additional_account(self, message, player_tag):
        user=message.from_user
        primary=self.get_registered_user(user.id)
        if not primary:
            return {"_needs_primary":True}
        tag=str(player_tag or "").upper().replace("#","").strip()
        primary_tag=str(primary.get("player_tag") or "").upper().replace("#","").strip()
        if tag == primary_tag:
            return {"_duplicate":True,"tag":"#"+tag}
        primary_owner=self._get("community_members",{"select":"telegram_user_id","player_tag":f"eq.{tag}","limit":"1"})
        extra_owner=self._get("community_member_accounts",{"select":"telegram_user_id","player_tag":f"eq.{tag}","is_active":"eq.true","limit":"1"})
        if primary_owner or extra_owner:
            return {"_tag_in_use":True,"tag":"#"+tag}
        player=self.player_fetcher(tag)
        if not player:
            return None
        existing=self.additional_accounts(user.id)
        order=max([int(x.get("account_order") or 1) for x in existing] or [1])+1
        self._post("community_member_accounts",{"telegram_user_id":int(user.id),"player_tag":str(player["tag"]).replace("#","").upper(),"player_name":player.get("name"),"account_order":order,"is_active":True,"updated_at":self._now_iso()},prefer="return=minimal")
        if self.snapshot_saver and player.get("trophies") is not None:
            try: self.snapshot_saver(player["tag"],player["name"],player["trophies"])
            except Exception as exc: print("ERRORE SNAPSHOT ACCOUNT AGGIUNTIVO:",repr(exc),flush=True)
        player["_account_order"]=order
        return player

    def my_accounts_text(self, telegram_user_id):
        primary=self.get_registered_user(telegram_user_id)
        if not primary:
            return "Non hai ancora un account principale registrato. Usa: registrami #TAG"
        lines=["I TUOI ACCOUNT","",f"PRINCIPALE - {primary.get('player_name') or 'Account'} #{primary.get('player_tag')}"]
        for row in self.additional_accounts(telegram_user_id):
            n=max(1,int(row.get("account_order") or 2)-1)
            lines.append(f"SECONDARIO {n} - {row.get('player_name') or 'Account'} #{row.get('player_tag')}")
        return "\n".join(lines)

    @staticmethod
    def _telegraph_nodes(lines):
        nodes = []
        first_value = next((str(item or "").strip() for item in lines if str(item or "").strip()), "")
        is_ranking_report = first_value.upper().startswith("CLASSIFICA")
        is_skin_account = first_value.upper().startswith("SKIN POSSEDUTE — ACCOUNT") or first_value.upper().endswith(" — SKIN")
        is_skin_catalog = first_value.upper().startswith("SKIN — ")
        is_command_guide = first_value.upper().startswith(("COMANDI SENS GPT", "GUIDA COMANDI"))
        is_dashboard = first_value.upper().startswith("CLASSIFICHE")
        # A detailed ranking has continuation/stat lines between numbered players.
        # One-line rankings stay compact; multi-line player blocks get visual
        # separation before every player across every Telegraph ranking.
        nonempty_values = [str(item or "").strip() for item in lines if str(item or "").strip()]
        ranking_positions = [i for i, item in enumerate(nonempty_values) if re.match(r"^\d+\.\s+", item)]
        is_detailed_ranking = bool(
            is_ranking_report
            and any(
                b - a > 1
                for a, b in zip(ranking_positions, ranking_positions[1:])
            )
        )
        # A combined Report begins with REPORT, but its Progressione section
        # has multi-line player blocks that need their own visual separation.
        in_report_progression = False
        progression_position = 0
        in_battle_logs = False
        battle_log_position = 0
        section_headings = {
            "PROFILO": "👤 PROFILO",
            "RANKED": "🏅 RANKED",
            "VITTORIE": "🏆 VITTORIE",
            "COLLEZIONE": "🎁 COLLEZIONE",
            "LIVELLI BRAWLER": "⚡ LIVELLI BRAWLER",
            "PRESTIGIO BRAWLER": "🌟 PRESTIGIO BRAWLER",
            "TEMPO DI GIOCO": "⏱️ TEMPO DI GIOCO",
            "COSTO PER MAXARE L'ACCOUNT": "💰 COSTO PER MAXARE L'ACCOUNT",
            "ANDAMENTO TROFEI": "📊 ANDAMENTO TROFEI",
            "DETTAGLIO BRAWLER": "🎯 DETTAGLIO BRAWLER",
            "LOG BATTAGLIE": "🎮 LOG BATTAGLIE",
            "SESSIONI": "🕹️ SESSIONI",
            "SQUADRA": "👥 SQUADRA",
        }
        field_emojis = {
            "data": "📅", "periodo": "🗓️", "partite osservate valide": "🎮",
            "sessioni osservate": "🕹️", "vittorie": "✅", "sconfitte": "❌",
            "coppe positive": "🏆", "bonus": "⚡", "coeff. progressione": "📊",
            "coppe nette": "🏆", "coppe osservate": "🏆", "coppe": "🏆",
            "punteggio progressione": "📈", "punti progressione": "📈", "punti": "📈",
            "valore difficoltà": "⚖️", "extra osservati vs delta base": "✨",
            "extra osservato": "✨", "delta base previsto": "🧮",
            "serie di vittorie osservata": "🔥", "peso fascia iniziale": "⚖️",
            "fasce": "🎯", "orario": "🕐", "squadra": "👥",
            "massimo squadra": "👑", "trofei": "🏆", "brawler": "🦸",
            "team value": "👑", "valore di riferimento": "🎯",
            "peso fascia di riferimento": "⚖️",
            "club": "🛡️", "tag": "🏷️", "tag club": "🏷️", "livello": "⚡",
            "punti esperienza": "✨", "fama": "🌠", "livello clip": "📎",
            "punti clip": "📎", "qualificazione championship": "🏆",
            "ranked attuale": "🏅", "record stagione": "📅", "record massimo": "👑",
            "skin": "🎨", "gadget": "🧰", "abilità stellari": "⭐",
            "equipaggiamenti": "⚙️", "overdrive": "🔥", "buffie": "💫",
            "prestigi totali": "🌟", "ore giocate stimate": "⏱️",
        }
        for index, raw in enumerate(lines):
            value = str(raw or "").strip()
            if not value:
                continue
            if is_dashboard and value.startswith("══ ") and value.endswith(" ══"):
                nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h3", "children": [value.strip("═ ")]}])
                continue
            if is_dashboard and re.match(r"^(?:🏆|🔥|📊) (?:Classifica|Progressione|Report)\b", value, re.I):
                prominence = "h3" if value.casefold().startswith("🏆 classifica trofei") else "h4"
                nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": prominence, "children": [value]}])
                continue
            if index > 0 and value == "🏆 CLASSIFICA TROFEI":
                nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h3", "children": [value]}])
                continue
            if first_value.upper().startswith("PROGRESSIONE") and value.startswith("🦸 "):
                nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h4", "children": [value]}])
                continue
            if is_skin_catalog and value.startswith("🦸 "):
                nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h4", "children": [value]}])
                continue
            if is_skin_account and value in ("📊 PER RARITÀ", "📊 PER CATEGORIA"):
                nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h3", "children": [value]}])
                continue
            if is_skin_account and re.match(r"^(?:🟢|🔵|🟣|🔴|🟡|🔥|🏅|🥈|🥇|🎨|🎮|🎟️|⚪) .+", value):
                if ":" not in value:
                    nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h4", "children": [value]}])
                    continue
            if first_value.upper().startswith("🔥 REPORT"):
                if "CLASSIFICA PROGRESSIONE" in value.upper():
                    in_report_progression = True
                    progression_position = 0
                    nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h3", "children": [value]}])
                    continue
                elif "RESOCONTO" in value.upper():
                    in_report_progression = False
                    nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h3", "children": [value]}])
                    continue
            command_name = re.fullmatch(r"\[\[CMDNAME:(.+?)\]\]", value)
            skin_link = re.fullmatch(r"\[\[SKINLINK:(https://telegra\.ph/[^\s|<>\[\]]+)\|(.+?)\]\]", value)
            if skin_link:
                nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h4", "children": [{
                    "tag": "a", "attrs": {"href": skin_link.group(1)}, "children": [skin_link.group(2)],
                }]}])
                continue
            skin_photo = re.fullmatch(
                r"\[\[SKINPHOTO:(https://cdn\.bsinfox\.com/brawlers/skins/\d+\.(?:webp|png))\|(.+?)\]\]",
                value,
            )
            if skin_photo:
                nodes.append({"tag": "p", "children": [{"tag": "a", "attrs": {"href": skin_photo.group(1)},
                                                       "children": [skin_photo.group(2)]}]})
                continue
            skin_jump = re.fullmatch(r"\[\[SKINJUMP:([A-Za-z0-9%_-]+)\|(.+?)\]\]", value)
            if is_skin_catalog and skin_jump:
                nodes.append({"tag": "p", "children": [{"tag": "a", "attrs": {"href": "#" + skin_jump.group(1)},
                                                       "children": [skin_jump.group(2)]}]})
                continue
            skin_brawler = re.fullmatch(r"\[\[SKINBRAWLER:([^<>\[\]]+)\]\]", value)
            if is_skin_catalog and skin_brawler:
                nodes.extend([{"tag": "p", "children": ["\u00a0"]},
                              {"tag": "h4", "children": [skin_brawler.group(1)]}])
                continue
            if command_name:
                command_text = command_name.group(1)
                if is_command_guide:
                    nodes.append({"tag": "p", "children": ["\u00a0"]})
                encoded = base64.urlsafe_b64encode(command_text.encode("utf-8")).decode("ascii").rstrip("=")
                if len("run_" + encoded) <= 64:
                    nodes.append({"tag": "p", "children": [{
                        "tag": "a",
                        "attrs": {"href": f"https://t.me/SensGPT_TitaniAbusiviBot?start=run_{encoded}"},
                        "children": [f"▶️ {command_text}"],
                    }]})
                else:
                    nodes.append({"tag": "p", "children": [{"tag": "strong", "children": [f"⌨️ {command_text}"]}]})
                continue
            command_link = re.fullmatch(r"\[\[CMD:([a-z0-9_]+)\|(.+?)\]\]", value, re.I)
            if command_link:
                payload, label = command_link.groups()
                if is_command_guide:
                    nodes.append({"tag": "p", "children": ["\u00a0"]})
                nodes.append({"tag": "p", "children": [{
                    "tag": "a",
                    "attrs": {"href": f"https://t.me/SensGPT_TitaniAbusiviBot?start={payload}"},
                    "children": [f"▶️ {label}"],
                }]})
                continue
            guide_link = value[len("[[GUIDE:"):-2].split("|", 1) if value.startswith("[[GUIDE:") and value.endswith("]]") else None
            if guide_link and len(guide_link) == 2 and re.fullmatch(r"https://telegra\.ph/[^\s|<>\[\]]+", guide_link[0]):
                nodes.append({"tag": "p", "children": [{
                    "tag": "a", "attrs": {"href": guide_link[0]},
                    "children": [f"📖 {guide_link[1]}"],
                }]})
                continue
            if is_command_guide and value.startswith("⌨️ "):
                nodes.append({"tag": "p", "children": [{"tag": "strong", "children": [value]}]})
                continue
            dashboard_link = re.fullmatch(r"\[\[DASH:(dash_[prt]_(?:[0-9]|10)_(?:0|7|15|30))\|Apri\]\]", value)
            direct_link = re.fullmatch(r"\[\[URL:(https://telegra\.ph/[^\s|<>\[\]]+)\|Apri\]\]", value)
            if direct_link:
                nodes.append({"tag": "p", "children": [{
                    "tag": "a", "attrs": {"href": direct_link.group(1)},
                    "children": ["📖 Apri il Telegraph"],
                }]})
                continue
            if dashboard_link:
                nodes.append({"tag": "p", "children": [{
                    "tag": "a", "attrs": {"href": f"https://t.me/SensGPT_TitaniAbusiviBot?start={dashboard_link.group(1)}"},
                    "children": ["📖 Apri"],
                }]})
                continue
            ranking = re.match(r"^(\d+)\.\s*(.+)$", value)
            if ranking:
                position = int(ranking.group(1))
                medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(position) if is_ranking_report else None
                rendered = f"{medal} {ranking.group(2)}" if medal else value
                children = [{"tag": "strong", "children": [rendered]}]
                # Telegraph spacing rule shared by every report:
                # compact one-line ranking rows stay adjacent; detailed multi-line
                # player blocks are visually separated so names/stats never merge.
                if in_report_progression:
                    progression_position += 1
                if in_battle_logs:
                    battle_log_position += 1
                if ((is_detailed_ranking and position > 1)
                        or (in_report_progression and progression_position > 1)
                        or (in_battle_logs and battle_log_position > 1)):
                    nodes.append({"tag": "p", "children": ["\u00a0"]})
                nodes.append({"tag": "p", "children": children})
                continue
            command_sections = {
                "👤 ACCOUNT E PROFILO", "🎨 SKIN ACCOUNT", "🏆 CLASSIFICHE & REPORT",
                "📑 REPORT PERIODICI — TROFEI + PROGRESSIONE", "🏆 CLASSIFICHE COMMUNITY",
                "📅 ACCESSI RAPIDI PER PERIODO", "⚡ COMANDI DIRETTI DI OGGI",
                "🛡️ CLASSIFICHE DEI 4 CLUB", "📊 GRAFICI",
                "🎯 DRAFT RANKED — SOLO CHAT PRIVATA", "🤝 COMMUNITY",
                "🎮 BRAWL STARS — RICHIESTE LIBERE",
            }
            if is_command_guide and value in command_sections:
                nodes.extend([{"tag": "p", "children": ["\u00a0"]}, {"tag": "h3", "children": [value]}])
                continue
            heading = section_headings.get(value.upper())
            if heading:
                if value.upper() == "LOG BATTAGLIE":
                    in_battle_logs = True
                if nodes:
                    nodes.append({"tag": "p", "children": ["\u00a0"]})
                nodes.append({"tag": "h3", "children": [heading]})
                continue
            if index == 0:
                title_icon = "📊" if is_dashboard else ("🎨" if is_skin_account else ("🏆" if value.upper().startswith("CLASSIFICA") else ("📈" if value.upper().startswith("PROGRESSIONE") else "👤")))
                nodes.append({"tag": "h3", "children": [f"{title_icon} {value}"]})
                continue
            if ":" in value:
                label, detail = value.split(":", 1)
                normalized_label = label.strip().casefold()
                if normalized_label == "risultato":
                    normalized_result = detail.strip().casefold()
                    if normalized_result.startswith("vittoria"):
                        icon = "✅"
                    elif normalized_result.startswith("sconfitta"):
                        icon = "❌"
                    elif normalized_result.startswith("pareggio"):
                        icon = "🤝"
                    else:
                        icon = "ℹ️"
                else:
                    icon = field_emojis.get(normalized_label)
                if icon:
                    nodes.append({"tag": "p", "children": [
                        {"tag": "strong", "children": [f"{icon} {label.strip()}:"]},
                        detail,
                    ]})
                    if is_ranking_report and normalized_label == "data":
                        nodes.extend([
                            {"tag": "p", "children": ["\u00a0"]},
                            {"tag": "p", "children": ["\u00a0"]},
                        ])
                    continue
            nodes.append({"tag": "p", "children": [value]})
        return nodes

    @staticmethod
    def _telegraph_reply(summary_lines, report_url, fallback_lines):
        return {
            "text": "\n".join(str(x) for x in summary_lines),
            "report_url": report_url,
            "fallback": "\n".join(str(x) for x in fallback_lines),
        }

    @staticmethod
    def _telegram_fallback_links(value):
        """Keep Telegraph markers internal when a Telegram button cannot be sent."""
        return re.sub(
            r"\[\[URL:(https://telegra\.ph/[^\s|<>\[\]]+)\|Apri\]\]",
            r"📖 Apri il Telegraph: \1", str(value),
        )

    def admin_reset_primary_registration(self, telegram_user_id):
        rows=self._get("community_members",{"select":"*","telegram_user_id":f"eq.{int(telegram_user_id)}","limit":"1"}) or []
        if not rows:
            return None
        row=rows[0]
        self._patch("community_members",{
            "player_tag":None,"player_name":None,"trophies":None,"club_name":None,
            "ranked_current":None,"ranked_peak":None,"ranked_season_peak":None,
            "ranked_current_elo":None,"ranked_peak_elo":None,"ranked_season_peak_elo":None,
            "ranked_career_peak":None,"ranked_career_peak_elo":None,
            "player_last_updated_at":None,
        },params={"telegram_user_id":f"eq.{int(telegram_user_id)}"})
        return row

    def admin_secondary_accounts_text(self, telegram_user_id):
        rows=self.additional_accounts(telegram_user_id)
        if not rows:
            return "Nessun account secondario attivo per questo utente."
        lines=["ACCOUNT SECONDARI DA RIPRISTINARE",""]
        for row in rows:
            n=max(1,int(row.get("account_order") or 2)-1)
            lines.append(f"{n}. {row.get('player_name') or 'Account'} #{row.get('player_tag')}")
        lines.append("")
        lines.append("Usa: ripristina registrazione secondario @utente N")
        return "\n".join(lines)

    def admin_reset_secondary_registration(self, telegram_user_id, secondary_number):
        rows=self.additional_accounts(telegram_user_id)
        target=None
        for row in rows:
            n=max(1,int(row.get("account_order") or 2)-1)
            if n == int(secondary_number):
                target=row
                break
        if not target:
            return None
        self._patch("community_member_accounts",{"is_active":False,"updated_at":self._now_iso()},params={"telegram_user_id":f"eq.{int(telegram_user_id)}","player_tag":f"eq.{target.get('player_tag')}"})
        return target

    def update_member_ranked(self, chat_id, user_id, ranked_current=None, ranked_peak=None):
        payload = {}
        if ranked_current is not None:
            payload["ranked_current"] = ranked_current[:80]
        if ranked_peak is not None:
            payload["ranked_peak"] = ranked_peak[:80]

        if payload:
            self._patch(
                "community_members",
                payload,
                params={
                    "chat_id": f"eq.{int(chat_id)}",
                    "telegram_user_id": f"eq.{int(user_id)}",
                },
            )

    def get_member_by_player_tag(self, chat_id, player_tag):
        tag = player_tag.upper().replace("#", "").strip()
        try:
            rows = self._get(
                "community_members",
                {
                    "select": "*",
                    "chat_id": f"eq.{int(chat_id)}",
                    "player_tag": f"eq.{tag}",
                    "limit": 1,
                },
            )
            return rows[0] if rows else None
        except Exception as exc:
            print("ERRORE LETTURA MEMBER TAG:", repr(exc), flush=True)
            return None

    def _member_current_trophies(self, member):
        # Leaderboards must be instant. The background trophy monitor already
        # refreshes official Supercell snapshots; never perform one live network
        # request per member while a Telegram command is waiting.
        tag = member.get("player_tag")
        if not tag:
            return None
        history = self.history_fetcher(tag, days=120)
        if history:
            try:
                return int(history[-1]["trophies"])
            except Exception:
                pass
        cached = member.get("trophies")
        try:
            return int(cached) if cached is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _window_trophy_values(history, start, end):
        """Use snapshots at the boundaries; never include a later calendar period."""
        snapshots = []
        for row in history or []:
            try:
                instant = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
                snapshots.append((instant, int(row["trophies"])))
            except (KeyError, ValueError, TypeError):
                continue
        snapshots.sort()
        before = [value for instant, value in snapshots if instant <= start]
        in_window = [(instant, value) for instant, value in snapshots if start <= instant < end]
        baseline = before[-1] if before else (in_window[0][1] if in_window else None)
        ending = next((value for instant, value in reversed(snapshots) if instant < end), None)
        return baseline, ending

    @staticmethod
    def _window_history_days(start):
        return max(10, (datetime.now(timezone.utc) - start.astimezone(timezone.utc)).days + 3)

    def ranking(self, chat_id, days=7, window=None):
        members = self.members(chat_id)
        def rank_member(member):
            tag = member.get("player_tag")
            if not tag:
                return None
            if window:
                history = self.history_fetcher(tag, days=self._window_history_days(window[0]))
                baseline, current = self._window_trophy_values(history, *window)
                delta = current - baseline if current is not None and baseline is not None else None
            else:
                current = self._member_current_trophies(member)
                if current is None:
                    return None
                history = self.history_fetcher(tag, days=max(days + 2, 10))
                changes = self.change_calculator(history, current)
                key = {0: "today", 7: "7d", 15: "15d", 30: "30d", 90: "90d"}.get(days, "7d")
                delta = changes.get(key)
            if current is None:
                return None
            return {
                "name": member.get("player_name") or member.get("display_name") or tag,
                "tag": tag,
                "current": current,
                "delta": int(delta) if delta is not None else None,
            }
        if len(members) >= 16:
            with ThreadPoolExecutor(max_workers=min(6, len(members))) as executor:
                results = list(executor.map(rank_member, members))
        else:
            results = [rank_member(member) for member in members]
        rows = [row for row in results if row is not None]
        rows.sort(
            key=lambda x: (
                x["delta"] is not None,
                x["delta"] if x["delta"] is not None else 0,
                x["current"],
            ),
            reverse=True,
        )
        return rows

    def ranked_history_rows(self, player_tag, days=10):
        """Read chronological Ranked ELO history for one registered player."""
        if not self.ready:
            return []
        tag = str(player_tag or "").upper().replace("#", "").strip()
        since = (datetime.now(timezone.utc) - timedelta(days=max(days + 2, 10))).isoformat()
        try:
            rows = self._request(
                "GET", "ranked_history",
                params={
                    "player_tag": f"eq.{tag}",
                    "ranked_current_elo": "not.is.null",
                    "recorded_at": f"gte.{since}",
                    "select": "ranked_current,ranked_current_elo,recorded_at",
                    "order": "recorded_at.asc",
                    "limit": "5000",
                },
            )
            invalid = {"unranked", "unknown", "ranked unknown", "non classificato", "–", "-"}
            return [
                row for row in (rows or [])
                if str(row.get("ranked_current") or "").strip().casefold() not in invalid
            ]
        except Exception as exc:
            print("ERRORE LETTURA STORICO ELO:", repr(exc), flush=True)
            return []

    @staticmethod
    def _ranked_state_at_or_before(history, target):
        selected = None
        for row in history:
            try:
                dt = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
                if dt <= target:
                    selected = {
                        "elo": int(row["ranked_current_elo"]),
                        "rank": row.get("ranked_current"),
                    }
                else:
                    break
            except Exception:
                continue
        return selected

    @staticmethod
    def _current_ranked_season_start(now):
        """Current Ranked season starts on the third Thursday of the month."""
        local = now.astimezone(ROME)
        first = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days_to_thursday = (3 - first.weekday()) % 7
        third_thursday = first + timedelta(days=days_to_thursday + 14)
        if local < third_thursday:
            prev_last = first - timedelta(days=1)
            prev_first = prev_last.replace(day=1)
            days_to_thursday = (3 - prev_first.weekday()) % 7
            third_thursday = prev_first + timedelta(days=days_to_thursday + 14)
        return third_thursday.astimezone(timezone.utc)

    def ranked_elo_ranking(self, chat_id, days=0, club_name=None):
        """Rank current-season ELO movement without counting the monthly reset as a loss."""
        now = datetime.now(timezone.utc)
        start_today = now.astimezone(ROME).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc)
        season_start = self._current_ranked_season_start(now)
        requested_target = start_today if days == 0 else now - timedelta(days=days)
        target = max(requested_target, season_start)
        rows = []
        for member in self.members(chat_id):
            tag = member.get("player_tag")
            if not tag:
                continue
            player = self.player_fetcher(tag)
            if not player:
                continue
            actual_club = self._club_name_from_player(player)
            if club_name and (actual_club or "").casefold() != club_name.casefold():
                continue
            current = player.get("ranked_current_elo")
            if current is None:
                current = member.get("ranked_current_elo")
            try:
                current = int(current)
            except (TypeError, ValueError):
                continue
            history = self.ranked_history_rows(tag, days)
            baseline = self._ranked_state_at_or_before(history, target)
            # If there is no snapshot at/before the boundary, use the first
            # valid snapshot after it. This is essential on the first tracking
            # day of a new Ranked season (and for newly registered players).
            if baseline is None:
                lower_bound = season_start if requested_target < season_start else target
                for row in history:
                    try:
                        dt = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
                        if dt >= lower_bound:
                            baseline = {
                                "elo": int(row["ranked_current_elo"]),
                                "rank": row.get("ranked_current"),
                            }
                            break
                    except Exception:
                        continue
            delta = current - baseline["elo"] if baseline is not None else None
            rows.append({
                "name": player.get("name") or member.get("player_name") or member.get("display_name") or tag,
                "tag": tag,
                "current": current,
                "delta": delta,
                "rank": player.get("ranked_current") or member.get("ranked_current"),
                "previous_rank": (baseline or {}).get("rank"),
            })
        rows.sort(key=lambda x: (
            x["delta"] is not None,
            x["delta"] if x["delta"] is not None else 0,
            x["current"],
        ), reverse=True)
        return rows

    def ranked_elo_ranking_text(self, chat_id, days=0, club_name=None):
        rows = self.ranked_elo_ranking(chat_id, days, club_name)
        scope = club_name or "COMMUNITY"
        period = "OGGI" if days == 0 else f"{days} GIORNI"
        if not rows:
            return f"Nessun ELO Classificata disponibile per {scope}."
        lines = [f"CLASSIFICA {scope} - ELO CLASSIFICATA {period}", ""]
        for index, row in enumerate(rows[:60], 1):
            delta = row["delta"]
            delta_text = (
                ("+" if delta > 0 else "") + self.number_formatter(delta)
                if delta is not None else "storico non ancora disponibile"
            )
            rank_now = row.get("rank") or "Non disponibile"
            rank_before = row.get("previous_rank")
            rank_change = ""
            if rank_before and rank_now and rank_before.casefold() != rank_now.casefold():
                rank_change = f" - {rank_before} ↑ {rank_now}" if (delta or 0) > 0 else f" - {rank_before} ↓ {rank_now}"
            else:
                rank_change = f" - {rank_now}"
            lines.append(
                f"{index}. {row['name']} - {self.number_formatter(row['current'])} ELO"
                f"{rank_change} ({delta_text})"
            )
        return "\n".join(lines)

    CLUB_ALIASES = {
        "titani": "TITANI ABUSIVI", "titani abusivi": "TITANI ABUSIVI",
        "tamarri": "TAMARRI ABUSIVI", "tamarri abusivi": "TAMARRI ABUSIVI",
        "tornadi": "TORNADI ABUSIVI", "tornadi abusivi": "TORNADI ABUSIVI",
        "talenti": "TALENTI ABUSIVI", "talenti abusivi": "TALENTI ABUSIVI",
    }

    # Official community club tags. Keep this mapping as the single source of
    # truth for census/profile fallbacks when live sources return only a club name.
    CLUB_TAGS = {
        "TITANI ABUSIVI": "#UG9Q8PC",
        "TAMARRI ABUSIVI": "#20CR900P9",
        "TORNADI ABUSIVI": "#80LUCQYGJ",
        "TALENTI ABUSIVI": "#82PQGGCVP",
    }

    # Graphic ranking cards use the matching Brawl Stars stat artwork when available.
    # URLs are resolved at runtime by the card renderer; text output remains the fallback.
    STAT_ICON_KEYS = {
        "trofei": "trophy",
        "brawler": "brawlers",
        "livello": "experience",
        "prestigio": "prestige",
        "3v3": "3v3",
        "solo": "solo",
        "duo": "duo",
        "classificata": "ranked",
        "classificata stagione": "ranked",
        "classificata carriera": "ranked",
    }

    STAT_DEFS = {
        "trofei": ("trophies", "Trofei"),
        "brawler": ("brawlers", "Brawler"),
        "livello": ("level", "Livello account"),
        "prestigio": ("prestige", "Prestigio"),
        "3v3": ("wins_3v3", "Vittorie 3v3"),
        "solo": ("wins_solo", "Vittorie Solo"),
        "duo": ("wins_duo", "Vittorie Duo"),
        "classificata": ("ranked_current_elo", "Classificata attuale"),
        "classificata stagione": ("ranked_season_peak_elo", "Record Classificata stagione"),
        "classificata carriera": ("ranked_career_peak_elo", "Record Classificata carriera"),
    }

    def _club_name_from_player(self, player):
        value=(player or {}).get("club_name") or (player or {}).get("club")
        if isinstance(value, dict): value=value.get("name")
        return str(value).strip() if value else None

    def _stat_rows(self, chat_id, stat_key, club_name=None):
        definition=self.STAT_DEFS.get(stat_key)
        if not definition: return []
        field,_=definition
        rows=[]
        for member in self.members(chat_id):
            tag=member.get("player_tag")
            if not tag: continue
            # Trophy leaderboards use the official Supercell snapshots refreshed
            # by the background monitor; do not make one live HTTP request/member.
            if stat_key == "trofei":
                actual_club = member.get("club_name")
                if club_name and (actual_club or "").casefold() != club_name.casefold():
                    continue
                value = self._member_current_trophies(member)
                if value is None:
                    continue
                player = {"name": member.get("player_name"), "tag": tag}
            else:
                player=self.player_fetcher(tag)
                if not player: continue
                normalized_tag=str(player.get("tag") or tag).upper().replace("#", "")
                if normalized_tag == "2VQYLG0RU8":
                    player["club"]="TALENTI ABUSIVI"
                    player["club_name"]="TALENTI ABUSIVI"
                actual_club=self._club_name_from_player(player)
                if club_name and (actual_club or "").casefold()!=club_name.casefold(): continue
                value=player.get(field)
            if value is None: continue
            try: value=int(value)
            except (TypeError,ValueError): continue
            display_value=None
            if stat_key == "classificata": display_value=player.get("ranked_current") or member.get("ranked_current")
            elif stat_key == "classificata stagione": display_value=player.get("ranked_season_peak") or member.get("ranked_season_peak")
            elif stat_key == "classificata carriera": display_value=player.get("ranked_career_peak") or player.get("ranked_peak") or member.get("ranked_career_peak") or member.get("ranked_peak")
            rows.append({"name":player.get("name") or member.get("player_name") or member.get("display_name") or tag,"value":value,"display_value":display_value,"tag":tag,"icon_url":player.get("icon_url")})
        rows.sort(key=lambda x:x["value"],reverse=True)
        return rows

    def coefficient_text(self, player_tag):
        player = self.player_fetcher(str(player_tag or "").strip().lstrip("#").upper())
        if not player or player.get("trophies") is None:
            return "Non riesco a recuperare questo giocatore."
        result = calculate_trophy_coefficient(player.get("brawler_trophies"), player.get("trophies"))
        value = int(result["score"]) - int(result["official_total"])
        coefficient = f'{result["coefficient"]:.6f}'.replace(".", ",")
        return "\n".join([
            f'COEFFICIENTE ABUSIVO — {player.get("name") or player_tag}',
            "",
            f'Trofei: {self.number_formatter(result["official_total"])}',
            f'Punteggio per coefficiente: {self.number_formatter(result["score"])}',
            f'Valore coefficiente: {self.number_formatter(value)}',
            f'Coefficiente Abusivo: {coefficient}',
        ])

    def progression_detail_text(self, player_tag, days=0):
        """Avoid rebuilding the same player's Brawler pages on rapid retries."""
        tag = str(player_tag or "").strip().lstrip("#").upper()
        key = (id(self), tag, int(days), datetime.now(ROME).date() if int(days) == 0 else None)
        with _PROGRESSION_DETAIL_LOCK:
            flight = _PROGRESSION_DETAIL_FLIGHTS.setdefault(key, threading.Lock())
        with flight:
            cached = _PROGRESSION_DETAIL_CACHE.get(key)
            if cached and cached[0] > time.monotonic():
                LOG.info("PROGRESSION DETAIL REUSED: tag=%s days=%s", tag, days)
                return cached[1]
            payload = self._progression_detail_uncached(tag, days)
            if isinstance(payload, dict) and payload.get("report_url"):
                _PROGRESSION_DETAIL_CACHE[key] = (time.monotonic() + 120, payload)
            return payload

    def _progression_detail_uncached(self, player_tag, days=0):
        """Detailed observed progression report for one monitored player."""
        from trophy_coefficient import TROPHY_COEFFICIENT_BANDS, score_brawler_trophies
        tag = str(player_tag or "").strip().lstrip("#").upper()
        if not tag:
            return "Giocatore non disponibile."
        now_local = datetime.now(ROME)
        if days == 0:
            start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            period = "OGGI"
        else:
            start_local = now_local - timedelta(days=int(days))
            period = f"ULTIMI {int(days)} GIORNI"
        try:
            rows = self._get("observed_trophy_battles", {
                "select": "player_name,battle_time,brawler_name,brawler_trophies_before,mode,result,placement,trophy_change,expected_base_delta,observed_extra,current_win_streak,bonus_type,team_max_brawler_trophies,team_composition,raw_battle",
                "player_tag": f"eq.{tag}",
                "battle_time": f"gte.{start_local.astimezone(timezone.utc).isoformat()}",
                "trophy_change": "not.is.null",
                "brawler_trophies_before": "not.is.null",
                "order": "battle_time.asc",
                "limit": "5000",
            })
        except Exception as exc:
            LOG.error("PROGRESSION DETAIL ERROR: %r", exc)
            return "Non riesco a recuperare il dettaglio Progressione in questo momento."
        rows = [r for r in (rows or []) if not str(r.get("bonus_type") or "").startswith("excluded")]
        if not rows:
            return f"PROGRESSIONE ABUSIVA — {period}\n\nNessuna battaglia osservata valida nel periodo."

        def dt_local(value):
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(ROME)

        def weighted_delta(row):
            """Wins use the highest Brawler trophy value in the team; losses score zero."""
            t0 = max(0, int(row.get("brawler_trophies_before") or 0))
            d = int(row.get("trophy_change") or 0)
            if d <= 0:
                return 0.0
            return float(score_brawler_trophies(t0 + d) - score_brawler_trophies(t0))

        def weight_at(trophies):
            value = max(0, int(trophies or 0))
            for start, end, weight in TROPHY_COEFFICIENT_BANDS:
                if start <= value < end:
                    return float(weight)
            return 1.0

        try:
            catalog_rows = self._get("brawlers_catalog", {"select": "name_en,name_it"}) or []
        except Exception as exc:
            LOG.warning("PROGRESSION DETAIL LOCALIZATION ERROR: type=%s", type(exc).__name__)
            catalog_rows = []
        brawler_names_it = {
            str(item.get("name_en") or "").strip().casefold():
                str(item.get("name_it") or item.get("name_en") or "").strip()
            for item in catalog_rows if item.get("name_en")
        }

        def brawler_name_it(value):
            source = str(value or "").strip()
            return brawler_names_it.get(source.casefold()) or source or "Brawler"

        def team_context(row):
            team = row.get("team_composition")
            if isinstance(team, list) and team:
                return row.get("team_max_brawler_trophies"), team
            raw = row.get("raw_battle")
            battle = raw.get("battle") if isinstance(raw, dict) else None
            teams = battle.get("teams") if isinstance(battle, dict) else None
            if not isinstance(teams, list):
                return None, None
            for candidate in teams:
                if not isinstance(candidate, list):
                    continue
                members = []
                contains_target = False
                for member in candidate:
                    if not isinstance(member, dict):
                        continue
                    member_tag = str(member.get("tag") or "").replace("#", "").upper()
                    if member_tag == tag:
                        contains_target = True
                    member_brawler = member.get("brawler") if isinstance(member.get("brawler"), dict) else {}
                    try:
                        trophies = int(member_brawler.get("trophies"))
                    except (TypeError, ValueError):
                        trophies = None
                    members.append({
                        "tag": member_tag or None,
                        "name": member.get("name"),
                        "brawler_name": str(member_brawler.get("name") or "").strip() or None,
                        "brawler_trophies": trophies,
                    })
                if contains_target and members:
                    values = [member["brawler_trophies"] for member in members if member["brawler_trophies"] is not None]
                    return (max(values) if values else None), members
            return None, None

        def band_labels(lo, hi):
            a, b = sorted((max(0, lo), max(0, hi)))
            labels = []
            for start, end, weight in TROPHY_COEFFICIENT_BANDS:
                if b >= start and a < end:
                    labels.append(f"{start:,}–{end-1:,} ×{weight:.4f}".replace(",", ".").replace("×1.", "×1,").replace("×2.", "×2,"))
            if b >= 3000:
                labels.append("3.000+ ×1,0000")
            return labels

        grouped = {}
        for row in rows:
            grouped.setdefault(str(row.get("brawler_name") or "Brawler"), []).append(row)
        raw_total = sum(int(r.get("trophy_change") or 0) for r in rows)
        weighted_total = int(Decimal(str(sum(weighted_delta(r) for r in rows))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        extra_total = sum(int(r.get("observed_extra") or 0) for r in rows if r.get("observed_extra") is not None)
        total_wins = sum(str(r.get("result") or "").casefold() == "victory" for r in rows)
        total_losses = sum(str(r.get("result") or "").casefold() == "defeat" for r in rows)
        def results_line(wins, losses):
            decided = wins + losses
            rate = f"{100 * wins / decided:.1f}".replace(".", ",") if decided else "n.d."
            return f"Vittorie: {wins} · Sconfitte: {losses} · Win rate: {rate}{'%' if decided else ''}"
        name = next((r.get("player_name") for r in reversed(rows) if r.get("player_name")), tag)
        lines = [
            f"PROGRESSIONE ABUSIVA — {name}", f"Periodo: {period}",
            f"Partite osservate valide: {len(rows)}",
            results_line(total_wins, total_losses),
            f"Coppe nette: {'+' if raw_total > 0 else ''}{self.number_formatter(raw_total)}",
            f"Punteggio Progressione: {'+' if weighted_total > 0 else ''}{self.number_formatter(weighted_total)}",
            f"Valore difficoltà: {'+' if weighted_total-raw_total > 0 else ''}{self.number_formatter(weighted_total-raw_total)}",
            f"Extra osservati vs delta base: +{self.number_formatter(extra_total)}", "",
            "DETTAGLIO BRAWLER"
        ]
        ordered_brawlers = sorted(grouped.items(), key=lambda item: sum(weighted_delta(r) for r in item[1]), reverse=True)
        brawler_sections = {}
        for brawler, br in ordered_brawlers:
            section_start = len(lines)
            raw = sum(int(r.get("trophy_change") or 0) for r in br)
            positive = sum(max(0, int(r.get("trophy_change") or 0)) for r in br)
            lost = sum(min(0, int(r.get("trophy_change") or 0)) for r in br)
            weighted = int(Decimal(str(sum(weighted_delta(r) for r in br))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            bonus = weighted - positive
            progression_coefficient = (float(weighted) / positive) if positive > 0 else 0.0
            wins = sum(1 for r in br if str(r.get("result") or "").casefold() == "victory")
            losses = sum(1 for r in br if str(r.get("result") or "").casefold() == "defeat")
            starts = [int(r.get("brawler_trophies_before") or 0) for r in br]
            ends = [max(0, int(r.get("brawler_trophies_before") or 0)+int(r.get("trophy_change") or 0)) for r in br]
            first, last = dt_local(br[0]["battle_time"]), dt_local(br[-1]["battle_time"])
            lines += ["", f"🦸 {brawler_name_it(brawler)}", f"Orario: {first:%H:%M}–{last:%H:%M} | Partite: {len(br)}",
                      results_line(wins, losses),
                      f"Coppe osservate: {min(starts+ends):,}–{max(starts+ends):,}".replace(",", "."),
                      f"Coppe positive: +{self.number_formatter(positive)} | Coppe perse: {self.number_formatter(lost)} | Saldo: {'+' if raw > 0 else ''}{self.number_formatter(raw)}",
                      f"Bonus: {'+' if bonus > 0 else ''}{self.number_formatter(bonus)} | Progressione: {'+' if weighted > 0 else ''}{self.number_formatter(weighted)}",
                      f"Coeff. Progressione: {progression_coefficient:.6f}".replace(".", ","),
                      "Fasce: " + " · ".join(band_labels(min(starts+ends), max(starts+ends)))]
            sessions=[]; current=[]
            for r in br:
                t=dt_local(r["battle_time"])
                if current and (t-dt_local(current[-1]["battle_time"])).total_seconds()>1800:
                    sessions.append(current); current=[]
                current.append(r)
            if current: sessions.append(current)
            lines.append(f"Sessioni osservate: {len(sessions)}")
            for ss in sessions:
                sr=sum(int(x.get("trophy_change") or 0) for x in ss)
                sw=int(Decimal(str(sum(weighted_delta(x) for x in ss))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                lines.append(f"🕒 {dt_local(ss[0]['battle_time']):%H:%M}–{dt_local(ss[-1]['battle_time']):%H:%M}: {len(ss)} partite, {'+' if sr>0 else ''}{sr} coppe, {'+' if sw>0 else ''}{sw} punti")
            brawler_sections[brawler] = lines[section_start:]
        lines += ["", "LOG BATTAGLIE"]
        battle_sections = {}
        brawler_battle_counts = {}
        for row in rows:
            brawler_key = str(row.get("brawler_name") or "Brawler")
            brawler_battle_counts[brawler_key] = brawler_battle_counts.get(brawler_key, 0) + 1
            index = brawler_battle_counts[brawler_key]
            battle_start = len(lines)
            t0 = max(0, int(row.get("brawler_trophies_before") or 0))
            delta = int(row.get("trophy_change") or 0)
            t1 = max(0, t0 + delta)
            mode_key = str(row.get("mode") or "").casefold()
            max_trophies, team = team_context(row)
            reference = t0
            points = weighted_delta(row)
            lines += [
                "",
                f"{index}. {dt_local(row['battle_time']):%H:%M} — {self._progression_mode_it(row.get('mode'))}",
                f"Brawler: {brawler_name_it(row.get('brawler_name'))}",
                f"Coppe: {t0} → {t1} ({'+' if delta > 0 else ''}{delta})",
                f"Risultato: {self._progression_result_it(row.get('result'), row.get('placement'))}",
                f"Valore di riferimento: {reference} 🏆",
                f"Peso fascia di riferimento: ×{weight_at(reference):.4f}".replace(".", ","),
                ("Punti Progressione: 0 (sconfitta non conteggiata)" if delta < 0 else
                 f"Punti Progressione: {'+' if points > 0 else ''}{points:.2f}".replace(".", ",")),
            ]
            expected = row.get("expected_base_delta")
            if expected is not None:
                lines.append(f"Delta base previsto: {'+' if int(expected) > 0 else ''}{int(expected)}")
            extra = row.get("observed_extra")
            if extra is not None and int(extra) != 0:
                lines.append(
                    f"Extra osservato: +{int(extra)} ({self._progression_bonus_it(row.get('bonus_type'))})"
                )
            if isinstance(team, list) and team:
                members = []
                for member in sorted(team, key=lambda item: int(item.get("brawler_trophies") or 0), reverse=True):
                    member_trophies = member.get("brawler_trophies")
                    crown = (
                        " 👑" if max_trophies is not None and member_trophies is not None
                        and int(member_trophies) == int(max_trophies) else ""
                    )
                    members.append(
                        f"{member.get('name') or member.get('tag') or 'Giocatore'} — "
                        f"{brawler_name_it(member.get('brawler_name'))} — "
                        f"{member_trophies if member_trophies is not None else '?'} 🏆{crown}"
                    )
                lines.append("Squadra:")
                lines.extend(f"{('🥇', '🥈', '🥉')[index] if index < 3 else '👤'} {member}"
                             for index, member in enumerate(members))
            elif mode_key in {"soloshowdown", "solo"}:
                lines.append("Squadra: Modalità Solo")
            else:
                lines.append("Squadra: non disponibile nel battle log.")
            if row.get("current_win_streak") is not None:
                lines.append(f"Serie di vittorie osservata: {int(row['current_win_streak'])}")
            battle_sections.setdefault(str(row.get("brawler_name") or "Brawler"), []).extend(lines[battle_start:])
        lines.insert(1, f"Data: {datetime.now(ROME):%d/%m/%Y %H:%M}")
        full_lines = lines
        overview = full_lines[:full_lines.index("DETTAGLIO BRAWLER") + 1]
        for brawler, br in ordered_brawlers:
            detail = [f"PROGRESSIONE {brawler_name_it(brawler)} — {name}", f"Periodo: {period}",
                      "", "DETTAGLIO BRAWLER", *brawler_sections[brawler], "", "LOG BATTAGLIE",
                      *battle_sections.get(brawler, [])]
            detail_url = self._publish_telegraph(f"Progressione {brawler_name_it(brawler)} — {name} — {period}", detail)
            if not detail_url:
                overview = full_lines
                break
            points = int(Decimal(str(sum(weighted_delta(r) for r in br))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            overview.extend(["", f"🦸 {brawler_name_it(brawler)}", f"Partite: {len(br)} · Progressione: +{self.number_formatter(points)}",
                             results_line(sum(str(r.get("result") or "").casefold() == "victory" for r in br),
                                          sum(str(r.get("result") or "").casefold() == "defeat" for r in br)),
                             f"[[URL:{detail_url}|Apri]]"])
        lines = overview
        report_url = self._publish_telegraph(f"Progressione {name} — {period}", lines)
        if report_url:
            summary = [
                f"PROGRESSIONE ABUSIVA — {name}",
                f"Periodo: {period}",
                f"Partite osservate valide: {len(rows)}",
                results_line(total_wins, total_losses),
                f"Coppe nette: {'+' if raw_total > 0 else ''}{self.number_formatter(raw_total)}",
                f"Punteggio Progressione: {'+' if weighted_total > 0 else ''}{self.number_formatter(weighted_total)}",
            ]
            return self._telegraph_reply(summary, report_url, lines)
        return "\n".join(lines)

    def _publish_command_guide(self):
        """Publish category reference pages and link every guide entry to Telegraph."""
        with _DASHBOARD_CACHE_LOCK:
            cached = _DASHBOARD_CACHE.get(("commands", "guide"))
            if cached and cached[0] > time.monotonic():
                return cached[1]
        source = HELP_TEXT.splitlines()
        headings = {
            "👤 ACCOUNT E PROFILO", "🎨 SKIN ACCOUNT", "🏆 CLASSIFICHE & REPORT",
            "📅 ACCESSI RAPIDI PER PERIODO", "⚡ COMANDI DIRETTI DI OGGI",
            "🛡️ CLASSIFICHE DEI 4 CLUB", "📊 GRAFICI",
            "🎯 DRAFT RANKED — SOLO CHAT PRIVATA", "🤝 COMMUNITY",
            "📑 REPORT PERIODICI — TROFEI + PROGRESSIONE",
            "🎮 BRAWL STARS — RICHIESTE LIBERE",
        }
        groups = {}
        group = None
        for line in source:
            if line in headings:
                group = line
                groups[group] = []
            elif group is not None:
                groups[group].append(line)
        def command_label(line):
            if line.startswith("[[CMDNAME:") and line.endswith("]]"):
                return line[len("[[CMDNAME:"):-2]
            if line.startswith("[[CMD:") and line.endswith("]]") and "|" in line:
                return line.split("|", 1)[1][:-2]
            return None
        urls = {}
        for heading, entries in groups.items():
            details = [f"GUIDA COMANDI — {heading}", "Apri questa pagina per leggere sintassi e descrizioni. Per ottenere i dati personali scrivi il comando al bot.", ""]
            for line in entries:
                label = command_label(line)
                details.append(f"⌨️ {label}" if label else line)
            urls[heading] = self._publish_telegraph(f"Comandi — {heading}", details)
            if not urls[heading]:
                return None
        rendered = []
        group = None
        for line in source:
            if line in headings:
                group = line
            label = command_label(line)
            if label and group:
                rendered.append(f"[[GUIDE:{urls[group]}|{label}]]")
            else:
                rendered.append(line)
        guide_url = self._publish_telegraph("Comandi Sens GPT — TITANI ABUSIVI", rendered)
        payload = self._telegraph_reply(["COMANDI SENS GPT", "Apri la guida Telegraph: ogni voce porta alla sua categoria."], guide_url, rendered) if guide_url else None
        if payload:
            with _DASHBOARD_CACHE_LOCK:
                _DASHBOARD_CACHE[("commands", "guide")] = (time.monotonic() + 6 * 3600, payload)
        return payload

    def _publish_telegraph(self, title, lines):
        token = os.getenv("TELEGRAPH_ACCESS_TOKEN", "").strip()
        if not token:
            LOG.warning("TELEGRAPH UNAVAILABLE: access token not configured")
            return None
        content = self._telegraph_nodes(lines)

        def create_page(page_title, nodes):
            with _TELEGRAPH_PAGE_LOCK:
                for attempt in range(4):
                    response = requests.post(
                        "https://api.telegra.ph/createPage",
                        data={
                            "access_token": token,
                            "title": str(page_title)[:256],
                            "author_name": "TITANI ABUSIVI",
                            "content": __import__("json").dumps(nodes, ensure_ascii=False),
                            "return_content": "false",
                        },
                        timeout=20,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("ok"):
                        break
                    error = str(payload.get("error") or "unknown")
                    flood = re.fullmatch(r"FLOOD_WAIT_(\d+)", error)
                    if flood and attempt < 3:
                        delay = min(int(flood.group(1)) + 1, 30)
                        LOG.warning("TELEGRAPH FLOOD WAIT: seconds=%s attempt=%s", delay, attempt + 1)
                        time.sleep(delay)
                        continue
                    LOG.error("TELEGRAPH API ERROR: status=%s error=%s", response.status_code, error[:300])
                    return None
            url = payload.get("result", {}).get("url")
            if not url:
                LOG.error("TELEGRAPH API ERROR: status=%s error=missing_result_url", response.status_code)
                return None
            print(
                "TELEGRAPH PAGE CREATED: title=%s url=%s" % (str(page_title)[:120], url),
                flush=True,
            )
            return url

        try:
            json_module = __import__("json")
            if len(json_module.dumps(content, ensure_ascii=False).encode("utf-8")) <= 50000:
                return create_page(title, content)

            # In a split Skin category, an in-page #BRAWLER link cannot jump
            # from one part to another. Move the Brawler index to the parent
            # page and point every entry at the part holding its heading.
            skin_jumps = []
            if str(title).startswith("Skin "):
                retained = []
                for node in content:
                    children = node.get("children", [])
                    anchor = children[0] if len(children) == 1 and isinstance(children[0], dict) else {}
                    href = anchor.get("attrs", {}).get("href", "")
                    if href.startswith("#") and node.get("tag") == "p":
                        skin_jumps.append((href[1:], anchor.get("children", [""])[0]))
                    elif children == ["🧭 SCEGLI UN BRAWLER"]:
                        continue
                    else:
                        retained.append(node)
                if skin_jumps:
                    content = retained

            chunks = []
            current = []
            if skin_jumps:
                # Keep each Brawler's heading and skins in the same part.
                blocks = []
                block = []
                for node in content:
                    if node.get("tag") == "h4" and block:
                        blocks.append(block)
                        block = []
                    block.append(node)
                if block:
                    blocks.append(block)
                for block in blocks:
                    candidate = current + block
                    if current and len(json_module.dumps(candidate, ensure_ascii=False).encode("utf-8")) > 45000:
                        chunks.append(current)
                        current = []
                    current.extend(block)
            else:
                for node in content:
                    candidate = current + [node]
                    if current and len(json_module.dumps(candidate, ensure_ascii=False).encode("utf-8")) > 45000:
                        chunks.append(current)
                        current = [node]
                    else:
                        current = candidate
            if current:
                chunks.append(current)

            part_urls = []
            for index, chunk in enumerate(chunks, 1):
                part_title = f"{title} — Parte {index}/{len(chunks)}"
                part_url = create_page(part_title, chunk)
                if not part_url:
                    return None
                part_urls.append(part_url)

            index_nodes = [{"tag": "h3", "children": [f"📊 {title}"]}]
            if skin_jumps:
                by_heading = {
                    quote(node["children"][0].replace(" ", "-"), safe="-"): part
                    for part, chunk in enumerate(chunks)
                    for node in chunk if node.get("tag") == "h4" and node.get("children")
                    and isinstance(node["children"][0], str)
                }
                index_nodes.append({"tag": "h4", "children": ["🧭 SCEGLI UN BRAWLER"]})
                for fragment, label in skin_jumps:
                    part = by_heading.get(fragment)
                    if part is not None:
                        index_nodes.append({"tag": "p", "children": [{"tag": "a", "attrs": {
                            "href": part_urls[part] + "#" + fragment,
                        }, "children": [label]}]})
            for index, url in enumerate(part_urls, 1):
                index_nodes.append({
                    "tag": "p",
                    "children": [{"tag": "a", "attrs": {"href": url}, "children": [
                        f"📄 Apri parte {index}/{len(part_urls)}"
                    ]}],
                })
            return create_page(title, index_nodes)
        except Exception as exc:
            LOG.error("TELEGRAPH API ERROR: type=%s message=%s", type(exc).__name__, str(exc)[:300])
            return None

    def progression_brawler_text(self, player_tag, brawler_name, days=0):
        """Period battle-by-battle Progressione log for one Brawler."""
        from trophy_coefficient import TROPHY_COEFFICIENT_BANDS, score_brawler_trophies
        tag = str(player_tag or "").strip().lstrip("#").upper()
        wanted = str(brawler_name or "").strip()
        if not tag or not wanted:
            return "Giocatore o Brawler non disponibile."
        now_local = datetime.now(ROME)
        if int(days or 0) == 0:
            start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            period = "OGGI"
        else:
            start_local = now_local - timedelta(days=int(days))
            period = f"ULTIMI {int(days)} GIORNI"
        try:
            rows = self._get("observed_trophy_battles", {
                "select": "player_name,battle_time,brawler_name,brawler_trophies_before,mode,result,placement,trophy_change,expected_base_delta,observed_extra,current_win_streak,bonus_type,team_max_brawler_trophies,team_composition,raw_battle",
                "player_tag": f"eq.{tag}",
                "brawler_name": f"ilike.{wanted}",
                "battle_time": f"gte.{start_local.astimezone(timezone.utc).isoformat()}",
                "trophy_change": "not.is.null",
                "brawler_trophies_before": "not.is.null",
                "order": "battle_time.asc",
                "limit": "5000",
            })
        except Exception as exc:
            LOG.error("PROGRESSION BRAWLER ERROR: %r", exc)
            return "Non riesco a recuperare il log del Brawler in questo momento."
        rows = [x for x in (rows or []) if not str(x.get("bonus_type") or "").startswith("excluded")]
        matches = [x for x in rows if str(x.get("brawler_name") or "").casefold() == wanted.casefold()]
        if not matches:
            available_rows = self._get("observed_trophy_battles", {
                "select": "brawler_name",
                "player_tag": f"eq.{tag}",
                "battle_time": f"gte.{start_local.astimezone(timezone.utc).isoformat()}",
                "trophy_change": "not.is.null",
                "brawler_trophies_before": "not.is.null",
                "limit": "5000",
            })
            available = sorted({str(x.get("brawler_name") or "") for x in available_rows or [] if x.get("brawler_name")})
            suffix = ("\nBrawler osservati nel periodo: " + ", ".join(available)) if available else ""
            return f"Nessuna battaglia valida di {wanted} osservata nel periodo {period}.{suffix}"

        try:
            catalog_rows = self._get("brawlers_catalog", {"select": "name_en,name_it"}) or []
        except Exception as exc:
            LOG.warning("PROGRESSION BRAWLER LOCALIZATION ERROR: type=%s", type(exc).__name__)
            catalog_rows = []
        brawler_names_it = {
            str(item.get("name_en") or "").strip().casefold():
                str(item.get("name_it") or item.get("name_en") or "").strip()
            for item in catalog_rows if item.get("name_en")
        }

        def brawler_name_it(value):
            source = str(value or "").strip()
            return brawler_names_it.get(source.casefold()) or source or "Brawler"

        def local_dt(value):
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(ROME)

        def weight_at(trophies):
            t = max(0, int(trophies))
            for start, end, weight in TROPHY_COEFFICIENT_BANDS:
                if start <= t < end:
                    return float(weight)
            return 1.0

        def points(row):
            """Positive Progressione uses the highest Brawler trophy value in the team."""
            t0 = max(0, int(row.get("brawler_trophies_before") or 0))
            d = int(row.get("trophy_change") or 0)
            if d <= 0:
                return 0.0
            return float(score_brawler_trophies(t0 + d) - score_brawler_trophies(t0))

        def team_context(row):
            """Use normalized team data, with a safe fallback to saved official evidence."""
            team = row.get("team_composition")
            if isinstance(team, list) and team:
                return row.get("team_max_brawler_trophies"), team
            raw = row.get("raw_battle")
            battle = raw.get("battle") if isinstance(raw, dict) else None
            teams = battle.get("teams") if isinstance(battle, dict) else None
            if not isinstance(teams, list):
                return None, None
            for candidate in teams:
                if not isinstance(candidate, list):
                    continue
                members = []
                contains_target = False
                for member in candidate:
                    if not isinstance(member, dict):
                        continue
                    member_tag = str(member.get("tag") or "").replace("#", "").upper()
                    if member_tag == tag:
                        contains_target = True
                    member_brawler = member.get("brawler") if isinstance(member.get("brawler"), dict) else {}
                    try:
                        trophies = int(member_brawler.get("trophies"))
                    except (TypeError, ValueError):
                        trophies = None
                    members.append({
                        "tag": member_tag or None,
                        "name": member.get("name"),
                        "brawler_name": str(member_brawler.get("name") or "").strip() or None,
                        "brawler_trophies": trophies,
                    })
                if contains_target and members:
                    values = [m["brawler_trophies"] for m in members if m["brawler_trophies"] is not None]
                    return (max(values) if values else None), members
            return None, None

        name = next((x.get("player_name") for x in reversed(matches) if x.get("player_name")), tag)
        brawler = brawler_name_it(matches[-1].get("brawler_name") or wanted)
        raw = sum(int(x.get("trophy_change") or 0) for x in matches)
        pts = sum(points(x) for x in matches)
        positive = sum(max(0, int(x.get("trophy_change") or 0)) for x in matches)
        lost = sum(min(0, int(x.get("trophy_change") or 0)) for x in matches)
        bonus = pts - positive
        wins = sum(1 for x in matches if str(x.get("result") or "").casefold() == "victory")
        losses = sum(1 for x in matches if str(x.get("result") or "").casefold() == "defeat")
        draws = sum(1 for x in matches if str(x.get("result") or "").casefold() == "draw")
        decided = wins + losses
        win_rate = (wins * 100.0 / decided) if decided else 0.0
        progression_coefficient = (pts / positive) if positive > 0 else 0.0
        start_trophies = int(matches[0].get("brawler_trophies_before") or 0)
        last = matches[-1]
        current_trophies = max(0, int(last.get("brawler_trophies_before") or 0) + int(last.get("trophy_change") or 0))
        lines = [
            f"PROGRESSIONE {brawler.upper()} — {name}",
            f"Data: {datetime.now(ROME):%d/%m/%Y}",
            f"Periodo: {period}",
            f"Partite osservate valide: {len(matches)}",
            f"Vittorie: {wins}",
            f"Sconfitte: {losses}",
            f"Pareggi: {draws}",
            f"Win rate: {win_rate:.2f}%".replace(".", ","),
            f"Coppe positive: +{positive}",
            f"Coppe perse: {lost}",
            f"Saldo trofei: {'+' if raw > 0 else ''}{raw}",
            f"Bonus: {'+' if bonus > 0 else ''}{bonus:.2f}".replace(".", ","),
            f"Progressione: {'+' if pts > 0 else ''}{pts:.2f}".replace(".", ","),
            f"Coeff. Progressione: {progression_coefficient:.6f}".replace(".", ","),
            f"Trofei inizio periodo: {start_trophies}",
            f"Trofei ultimo dato osservato: {current_trophies}",
            "",
            "LOG BATTAGLIE",
        ]
        for index, row in enumerate(matches, 1):
            t0 = max(0, int(row.get("brawler_trophies_before") or 0))
            d = int(row.get("trophy_change") or 0)
            t1 = max(0, t0 + d)
            p = points(row)
            max_t, team = team_context(row)
            reference_trophies = t0
            w = weight_at(reference_trophies)
            expected = row.get("expected_base_delta")
            extra = row.get("observed_extra")
            mode = self._progression_mode_it(row.get("mode"))
            result = row.get("result")
            placement = row.get("placement")
            outcome = self._progression_result_it(result, placement)
            lines += [
                "",
                f"{index}. {local_dt(row['battle_time']):%H:%M} — {mode}",
                f"Coppe: {t0} → {t1} ({'+' if d > 0 else ''}{d})",
                f"Risultato: {outcome}",
                f"Valore di riferimento: {reference_trophies} 🏆",
                f"Peso fascia di riferimento: ×{w:.4f}".replace(".", ","),
                ("Punti Progressione: 0 (sconfitta non conteggiata)" if d < 0 else
                 f"Punti Progressione: {'+' if p > 0 else ''}{p:.2f}".replace(".", ",")),
            ]
            if expected is not None:
                lines.append(f"Delta base previsto: {'+' if int(expected) > 0 else ''}{int(expected)}")
            if extra is not None and int(extra) != 0:
                bonus = self._progression_bonus_it(row.get("bonus_type"))
                lines.append(f"Extra osservato: +{int(extra)} ({bonus})")
            if row.get("current_win_streak") is not None:
                lines.append(f"Serie di vittorie osservata: {int(row['current_win_streak'])}")

            if isinstance(team, list) and team:
                lines.append("Squadra:")
                for member in team:
                    mt = member.get("brawler_trophies")
                    crown = " 👑" if max_t is not None and mt is not None and int(mt) == int(max_t) else ""
                    lines.append(
                        f"• {member.get('name') or member.get('tag') or 'Giocatore'} — "
                        f"{brawler_name_it(member.get('brawler_name'))} — "
                        f"{mt if mt is not None else '?'} 🏆{crown}"
                    )
                if max_t is not None:
                    lines.append(f"Massimo squadra: {int(max_t)} 🏆")
            elif str(row.get("mode") or "").casefold() in {"soloshowdown", "solo"}:
                lines.append("Squadra: Modalità Solo")
            else:
                lines.append("Squadra: non disponibile nel battle log.")
        report_url = self._publish_telegraph(f"Progressione {brawler} — {name}", lines)
        if report_url:
            summary = [
                f"PROGRESSIONE {brawler.upper()} — {name}",
                "Periodo: OGGI",
                f"Partite osservate valide: {len(matches)}",
                "Coppe nette: " + ("+" if raw > 0 else "") + str(raw),
                "Punti Progressione: " + ("+" if pts > 0 else "") + f"{pts:.2f}".replace(".", ","),
            ]
            return self._telegraph_reply(summary, report_url, lines)
        return "\n".join(lines)

    def coefficient_ranking_text(self, chat_id, scope="community", days=None, window=None):
        scope = str(scope or "community").strip().casefold()
        club_name = None
        registered_only = False

        if scope == "community":
            # All registered users, regardless of club.
            members = self._get("community_members", {
                "select": "player_tag,player_name,display_name,club_name,is_active",
                "is_active": "eq.true",
                "player_tag": "not.is.null",
                "order": "player_last_updated_at.desc.nullslast",
                "limit": "1000",
            })
            title = "CLASSIFICA PROGRESSIONE"
        elif scope == "community_club":
            # Registered users who currently belong to one of the four ABUSIVI clubs.
            members = self._get("community_members", {
                "select": "player_tag,player_name,display_name,club_name,is_active",
                "is_active": "eq.true",
                "player_tag": "not.is.null",
                "order": "player_last_updated_at.desc.nullslast",
                "limit": "1000",
            })
            allowed = {name.casefold() for name in self.CLUB_ALIASES.values()}
            members = [m for m in (members or []) if str(m.get("club_name") or "").casefold() in allowed]
            title = "CLASSIFICA PROGRESSIONE CLUB"
        elif scope == "global_clubs":
            # Complete latest rosters of all four ABUSIVI clubs.
            members = []
            seen = set()
            for name in sorted(set(self.CLUB_ALIASES.values())):
                latest_dates = self._get("club_roster_daily", {
                    "select": "snapshot_date", "club_name": f"eq.{name}",
                    "order": "snapshot_date.desc", "limit": "1",
                })
                latest_date = latest_dates[0].get("snapshot_date") if latest_dates else None
                if not latest_date:
                    continue
                for member in self._get("club_roster_daily", {
                    "select": "player_tag,player_name,club_name",
                    "club_name": f"eq.{name}", "snapshot_date": f"eq.{latest_date}", "limit": "100",
                }) or []:
                    tag = str(member.get("player_tag") or "").strip().lstrip("#").upper()
                    if tag and tag not in seen:
                        seen.add(tag)
                        members.append(member)
            title = "CLASSIFICA PROGRESSIONE GLOBALE CLUB"
        else:
            global_single = False
            if scope.startswith("global_single:"):
                global_single = True
                scope = scope.split(":", 1)[1]
            club_name = self.CLUB_ALIASES.get(scope) or self.CLUB_ALIASES.get(scope.replace(" abusivi", ""))
            if not club_name:
                return "Classifica progressione non riconosciuta."
            if global_single:
                latest_dates = self._get("club_roster_daily", {
                    "select": "snapshot_date", "club_name": f"eq.{club_name}",
                    "order": "snapshot_date.desc", "limit": "1",
                })
                latest_date = latest_dates[0].get("snapshot_date") if latest_dates else None
                members = self._get("club_roster_daily", {
                    "select": "player_tag,player_name,club_name",
                    "club_name": f"eq.{club_name}", "snapshot_date": f"eq.{latest_date}", "limit": "100",
                }) if latest_date else []
                title = f"CLASSIFICA PROGRESSIONE CLUB GLOBALE — {club_name}"
            else:
                # Registered users of the requested club only.
                members = self._get("community_members", {
                    "select": "player_tag,player_name,display_name,club_name,is_active",
                    "is_active": "eq.true", "player_tag": "not.is.null",
                    "club_name": f"eq.{club_name}", "limit": "1000",
                })
                title = f"CLASSIFICA PROGRESSIONE — {club_name}"

        unique = {}
        for member in members or []:
            tag = str(member.get("player_tag") or "").strip().lstrip("#").upper()
            if tag and tag not in unique:
                unique[tag] = member
        member_by_tag = unique
        try:
            response = requests.post(
                f"{self.supabase_url}/rest/v1/rpc/{'coefficient_progression_rows_range' if window else 'coefficient_progression_rows_v2'}",
                headers=self._headers(),
                json=({"p_player_tags": list(member_by_tag), "p_start": window[0].isoformat(),
                       "p_end": window[1].isoformat()} if window else
                      {"p_player_tags": list(member_by_tag), "p_days": days}),
                timeout=20,
            )
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:
            LOG.error("COEFFICIENT RANKING RPC ERROR: %r", exc)
            rows = []
        for row in rows:
            member = member_by_tag.get(str(row.get("player_tag") or "").upper(), {})
            row["name"] = row.get("player_name") or member.get("player_name") or member.get("display_name") or row.get("player_tag")
            row["value"] = row.get("coefficient_value") if days is None else row.get("progression_value")
        rows = [row for row in rows if row.get("value") is not None
                and (days is None or int(row["value"]) > 0)]
        rows.sort(
            key=lambda row: (
                int(row["value"]),
                (float(row["value"]) / int(row.get("positive_trophies") or 0))
                if days is not None and int(row.get("positive_trophies") or 0) > 0 else 0,
            ),
            reverse=True,
        )
        if not rows:
            return f"{title}\n\nStorico non ancora disponibile per questo periodo."
        period = "ATTUALE" if days is None else ("OGGI" if days == 0 else f"{days} GIORNI")
        lines = [f"{title} — {period}", f"Data: {datetime.now(ROME):%d/%m/%Y %H:%M}", ""]
        for index, row in enumerate(rows[:200], 1):
            account_coefficient = f'{float(row["coefficient"]):.6f}'.replace(".", ",")
            value = int(row["value"])
            value_text = ("+" if value > 0 and days is not None else "") + self.number_formatter(value)
            if days is None:
                lines.append(f'{index}. {row["name"]} — Valore coefficiente: {value_text} — Coeff. Abusivo: {account_coefficient}')
            else:
                battles = int(row.get("battle_count") or 0)
                cups = int(row.get("positive_trophies") or 0)
                bonus = value - cups
                progression_coefficient = (float(value) / cups) if cups > 0 else 0.0
                progression_coefficient_text = f"{progression_coefficient:.6f}".replace(".", ",")
                cups_text = ("+" if cups > 0 else "") + self.number_formatter(cups)
                bonus_text = ("+" if bonus > 0 else "") + self.number_formatter(bonus)
                play_seconds = int(row.get("play_seconds") or 0)
                hours, remainder = divmod(play_seconds, 3600)
                minutes = remainder // 60
                play_time = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
                lines += [
                    f'{index}. {row["name"]}',
                    f'⏱️ Tempo di gioco: {play_time}',
                    f'🎮 Partite: {battles}',
                    f'🏆 Coppe: {cups_text}',
                    f'⚡ Bonus: {bonus_text}',
                    f'🔥 Progressione: {value_text}',
                    f'🧮 Coeff. Progressione: {progression_coefficient_text}',
                    "",
                ]
        report_url = self._publish_telegraph(f"{title} — {period}", lines)
        if report_url:
            if days is None:
                summary = [f"{title} — {period}", "", *lines[3:6]]
            else:
                # Three complete positions in Telegram; the full ranking stays on Telegraph.
                summary_lines = []
                positions = 0
                for line in lines[3:]:
                    if re.match(r"^\d+\.\s", line):
                        positions += 1
                        if positions > 3:
                            break
                    summary_lines.append(line)
                while summary_lines and not summary_lines[-1]:
                    summary_lines.pop()
                summary = [f"{title} — {period}", "", *summary_lines]
            return self._telegraph_reply(summary, report_url, lines)
        return "\n".join(lines)

    def stat_ranking_text(self, chat_id, stat_key, club_name=None):
        definition=self.STAT_DEFS.get(stat_key)
        if not definition: return "Statistica non riconosciuta."
        _,label=definition
        rows=self._stat_rows(chat_id,stat_key,club_name)
        scope=club_name or "COMMUNITY"
        if not rows: return f"Nessun dato disponibile per {label} in {scope}."
        lines=[f"CLASSIFICA {scope} - {label.upper()}",""]
        for i,row in enumerate(rows[:60],1):
            value = self.number_formatter(row['value'])
            if stat_key.startswith("classificata") and row.get("display_value"):
                value = f"{value} ELO - {row['display_value']}"
            lines.append(f"{i}. {row['name']} - {value}")
        return "\n".join(lines)

    def all_stats_text(self, chat_id, club_name=None):
        scope=club_name or "COMMUNITY"
        lines=[f"STATISTICHE E CLASSIFICHE - {scope}",""]
        for key,(_,label) in self.STAT_DEFS.items():
            rows=self._stat_rows(chat_id,key,club_name)
            if rows: lines.append(f"{label}: 1° {rows[0]['name']} - {self.number_formatter(rows[0]['value'])}")
        lines += ["","Classifiche disponibili: trofei, Brawler, livello, prestigio, 3v3, Solo, Duo, Classificata, Classificata stagione, Classificata carriera."]
        return "\n".join(lines)

    def wins_3v3_ranking_text(self, chat_id):
        rows = []
        for member in self.members(chat_id):
            tag = member.get("player_tag")
            if not tag:
                continue
            player = self.player_fetcher(tag)
            if not player or player.get("wins_3v3") is None:
                continue
            try:
                wins = int(player["wins_3v3"])
            except (TypeError, ValueError):
                continue
            rows.append({
                "name": player.get("name") or member.get("player_name") or member.get("display_name") or tag,
                "wins": wins,
            })
        rows.sort(key=lambda row: row["wins"], reverse=True)
        if not rows:
            return "Non riesco a recuperare le vittorie 3v3 dei giocatori registrati in questo momento."
        lines = ["CLASSIFICA COMMUNITY - VITTORIE 3V3", ""]
        for index, row in enumerate(rows[:60], 1):
            lines.append(
                f"{index}. {row['name']} - {self.number_formatter(row['wins'])} vittorie"
            )
        return "\n".join(lines)

    def ranking_text(self, chat_id, days=7, window=None):
        rows = self.ranking(chat_id, days, window=window) if window else self.ranking(chat_id, days)
        if days == 0:
            rows = [row for row in rows if row["delta"] is not None and row["delta"] > 0]
        if not rows:
            if days == 0:
                return "CLASSIFICA COMMUNITY - OGGI\n\nNessun giocatore con trofei guadagnati oggi."
            return (
                "Non ho ancora abbastanza giocatori registrati/storico trofei. "
                "Ogni membro può usare: registrami #TAG"
            )
        period_label = "OGGI" if days == 0 else f"{days} GIORNI"
        lines = [f"CLASSIFICA COMMUNITY - {period_label}", ""]
        for index, row in enumerate(rows[:60], 1):
            if row["delta"] is None:
                delta_text = "storico di oggi non disponibile" if days == 0 else f"storico {days}g non ancora disponibile"
            else:
                sign = "+" if row["delta"] > 0 else ""
                delta_text = f"{sign}{row['delta']}"
            lines.append(
                f"{index}. {row['name']} - {self.number_formatter(row['current'])} "
                f"({delta_text})"
            )
        return "\n".join(lines)

    def _complete_roster_daily_rows(self, start_date, end_date):
        """Read complete-roster daily snapshots for [start_date, end_date)."""
        try:
            return self._get("club_roster_daily", {
                "select": "snapshot_date,club_name,club_tag,player_tag,player_name,first_trophies,last_trophies,first_seen_at,last_seen_at,source",
                "and": f"(snapshot_date.gte.{start_date.isoformat()},snapshot_date.lt.{end_date.isoformat()})",
                "order": "snapshot_date.asc,player_tag.asc",
                "limit": "10000",
            }) or []
        except Exception as exc:
            print("ERRORE LETTURA ROSTER GLOBALE:", repr(exc), flush=True)
            return []

    def _roster_trophy_histories(self, tags, start, end):
        """Build snapshot histories for complete club rosters, including nonregistrants."""
        if not tags:
            return {}
        start_date = (start.astimezone(ROME).date() - timedelta(days=2)).isoformat()
        end_date = (end.astimezone(ROME).date() + timedelta(days=1)).isoformat()
        try:
            rows = self._get("club_roster_daily", {
                "select": "player_tag,first_trophies,last_trophies,first_seen_at,last_seen_at",
                "and": f"(snapshot_date.gte.{start_date},snapshot_date.lt.{end_date})",
                "limit": "10000",
            }) or []
        except Exception as exc:
            LOG.warning("GLOBAL ROSTER HISTORY FAILED: %s", type(exc).__name__)
            return {}
        wanted = set(tags)
        result = {}
        for row in rows:
            tag = str(row.get("player_tag") or "").strip().lstrip("#").upper()
            if tag not in wanted:
                continue
            for moment, trophies in (("first_seen_at", "first_trophies"),
                                      ("last_seen_at", "last_trophies")):
                try:
                    instant = datetime.fromisoformat(str(row[moment]).replace("Z", "+00:00"))
                    value = int(row[trophies])
                    if instant.tzinfo is None:
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                result.setdefault(tag, []).append({"recorded_at": instant.isoformat(), "trophies": value})
        for snapshots in result.values():
            snapshots.sort(key=lambda item: item["recorded_at"])
        return result

    @staticmethod
    def _previous_month_bounds(now=None):
        local = (now or datetime.now(timezone.utc)).astimezone(ROME)
        this_month = local.date().replace(day=1)
        previous_last = this_month - timedelta(days=1)
        return previous_last.replace(day=1), this_month

    @staticmethod
    def _daily_baseline_tags(rows, tolerance_minutes=15):
        """Tags captured with the club's initial daily roster; later joiners have no daily baseline."""
        first_by_club = {}
        parsed = []
        for row in rows:
            club = str(row.get("club_name") or "").strip().upper()
            tag = str(row.get("player_tag") or "").strip().upper()
            raw = row.get("first_seen_at")
            try:
                seen = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                seen = None
            parsed.append((club, tag, seen))
            if club and seen is not None and (club not in first_by_club or seen < first_by_club[club]):
                first_by_club[club] = seen
        tolerance = timedelta(minutes=tolerance_minutes)
        return {
            tag for club, tag, seen in parsed
            if tag and seen is not None and club in first_by_club
            and seen <= first_by_club[club] + tolerance
        }

    def global_ranking_text(self, chat_id, days=0):
        """All players in the four complete club rosters. Daily scope only."""
        today = datetime.now(timezone.utc).astimezone(ROME).date()
        rows = self._complete_roster_daily_rows(today, today + timedelta(days=1))
        baseline_tags = self._daily_baseline_tags(rows)
        players = []
        for row in rows:
            try:
                current = int(row["last_trophies"])
            except (TypeError, ValueError, KeyError):
                continue
            tag = str(row.get("player_tag") or "").strip().upper()
            delta = None
            if tag in baseline_tags:
                try:
                    delta = current - int(row["first_trophies"])
                except (TypeError, ValueError, KeyError):
                    delta = None
            players.append({"name": row.get("player_name") or row.get("player_tag"), "current": current, "delta": delta})
        players = [row for row in players if row["delta"] is not None and row["delta"] > 0]
        players.sort(key=lambda row: (row["delta"], row["current"]), reverse=True)
        lines = ["CLASSIFICA GLOBALE - OGGI", ""]
        if not players:
            lines.append("Nessun giocatore con trofei guadagnati oggi.")
            return "\n".join(lines)
        for index, row in enumerate(players[:200], 1):
            if row["delta"] is None:
                delta_text = "N/D"
            else:
                sign = "+" if row["delta"] > 0 else ""
                delta_text = f"{sign}{row['delta']}"
            lines.append(f"{index}. {row['name']} - {self.number_formatter(row['current'])} ({delta_text})")
        return "\n".join(lines)

    def global_club_ranking_text(self, chat_id, monthly=False):
        """Compare all four complete rosters, including non-registered players."""
        if monthly:
            start, end = self._previous_month_bounds()
            month_names = ["GENNAIO","FEBBRAIO","MARZO","APRILE","MAGGIO","GIUGNO","LUGLIO","AGOSTO","SETTEMBRE","OTTOBRE","NOVEMBRE","DICEMBRE"]
            title = f"CLASSIFICA GLOBALE CLUB - {month_names[start.month-1]} {start.year}"
        else:
            start = datetime.now(timezone.utc).astimezone(ROME).date(); end = start + timedelta(days=1)
            title = "CLASSIFICA GLOBALE CLUB - OGGI"
        source_rows = self._complete_roster_daily_rows(start, end)
        baseline_tags = None if monthly else self._daily_baseline_tags(source_rows)
        totals = {club: {"delta": 0, "players": set()} for club in self.CLUB_TAGS}
        for row in source_rows:
            club = str(row.get("club_name") or "").strip().upper()
            if club not in totals: continue
            tag = str(row.get("player_tag") or "").strip().upper()
            if baseline_tags is not None and tag not in baseline_tags:
                continue
            try: delta = int(row["last_trophies"]) - int(row["first_trophies"])
            except (TypeError, ValueError, KeyError): continue
            totals[club]["delta"] += delta
            if tag: totals[club]["players"].add(tag)
        ranked = sorted(totals.items(), key=lambda item: (item[1]["delta"], len(item[1]["players"])), reverse=True)
        lines = [title, ""]
        for index, (club, data) in enumerate(ranked, 1):
            delta = data["delta"]; sign = "+" if delta > 0 else ""
            lines.append(f"{index}. {club} - {sign}{self.number_formatter(delta)} ({len(data['players'])} giocatori)")
        return "\n".join(lines)

    def global_monthly_ranking_text(self, chat_id):
        """All-player ranking for the previous completed calendar month."""
        start, end = self._previous_month_bounds()
        source_rows = self._complete_roster_daily_rows(start, end)
        players = {}
        for row in source_rows:
            tag = str(row.get("player_tag") or "").strip()
            if not tag: continue
            try:
                delta = int(row["last_trophies"]) - int(row["first_trophies"]); current = int(row["last_trophies"])
            except (TypeError, ValueError, KeyError): continue
            item = players.setdefault(tag, {"name": row.get("player_name") or tag, "delta": 0, "current": current})
            item["delta"] += delta; item["current"] = current
            if row.get("player_name"): item["name"] = row["player_name"]
        ranked = sorted(players.values(), key=lambda row: (row["delta"], row["current"]), reverse=True)
        month_names = ["GENNAIO","FEBBRAIO","MARZO","APRILE","MAGGIO","GIUGNO","LUGLIO","AGOSTO","SETTEMBRE","OTTOBRE","NOVEMBRE","DICEMBRE"]
        lines = [f"CLASSIFICA GLOBALE - {month_names[start.month-1]} {start.year}", ""]
        if not ranked:
            lines.append("Storico roster completo non ancora disponibile per il mese precedente.")
            return "\n".join(lines)
        for index, row in enumerate(ranked[:200], 1):
            sign = "+" if row["delta"] > 0 else ""
            lines.append(f"{index}. {row['name']} - {self.number_formatter(row['current'])} ({sign}{row['delta']})")
        return "\n".join(lines)

    def club_trophy_ranking_text(self, chat_id, days=0, window=None):
        """Rank the four community clubs by summed trophy movement from stored snapshots."""
        clubs = list(self.CLUB_TAGS.keys())
        totals = {club: {"delta": 0, "players": 0} for club in clubs}
        for member in self.members(chat_id):
            club = str(member.get("club_name") or "").strip().upper()
            if club not in totals or not member.get("player_tag"):
                continue
            if window:
                history = self.history_fetcher(member["player_tag"], days=self._window_history_days(window[0]))
                baseline, endpoint = self._window_trophy_values(history, *window)
                delta = endpoint - baseline if endpoint is not None and baseline is not None else None
            else:
                current = self._member_current_trophies(member)
                if current is None:
                    continue
                history = self.history_fetcher(member["player_tag"], days=max(days + 2, 10))
                changes = self.change_calculator(history, current)
                key = "today" if days == 0 else {7: "7d", 15: "15d", 30: "30d", 90: "90d"}.get(days, "7d")
                delta = changes.get(key)
            if delta is None:
                continue
            totals[club]["delta"] += int(delta)
            totals[club]["players"] += 1

        rows = [
            (club, data["delta"], data["players"])
            for club, data in totals.items()
        ]
        rows.sort(key=lambda row: (row[1], row[2]), reverse=True)
        label = "OGGI" if days == 0 else f"{days} GIORNI"
        lines = [f"CLASSIFICA CLUB - TROFEI {label}", ""]
        for index, (club, delta, players) in enumerate(rows, 1):
            sign = "+" if delta > 0 else ""
            lines.append(f"{index}. {club} - {sign}{self.number_formatter(delta)} ({players} giocatori)")
        return "\n".join(lines)

    def club_summary_text(self, chat_id):
        members = self.members(chat_id)
        registered = [m for m in members if m.get("player_tag")]
        ranking7 = self.ranking(chat_id, 7)
        total = sum(r["current"] for r in ranking7)
        valid_growth = [r for r in ranking7 if r["delta"] is not None]
        growth7 = sum(r["delta"] for r in valid_growth)
        top = valid_growth[:3]
        lines = [
            "COMMUNITY ABUSIVI - PROFILO CLUB",
            "Ordine club: TITANI ABUSIVI > TAMARRI ABUSIVI > TORNADI ABUSIVI > TALENTI ABUSIVI",
            "",
            f"Membri Telegram tracciati: {len(members)}",
            f"Giocatori registrati: {len(registered)}",
        ]
        if ranking7:
            lines.extend(
                [
                    f"Trofei registrati complessivi: {self.number_formatter(total)}",
                    (f"Crescita complessiva 7 giorni: {'+' if growth7 > 0 else ''}{growth7}" if valid_growth else "Crescita complessiva 7 giorni: storico non ancora disponibile"),
                    "",
                    "Top crescita 7 giorni:" if valid_growth else "Top crescita 7 giorni: storico non ancora disponibile",
                ]
            )
            for row in top:
                lines.append(f"- {row['name']}: {'+' if row['delta'] > 0 else ''}{row['delta']}")
        else:
            lines.append("Storico trofei ancora insufficiente per il riepilogo competitivo.")
        return "\n".join(lines)

    def registered_members_text(self, chat_id):
        """Return the linked accounts for this chat, without exposing Telegram IDs."""
        registered = [member for member in self.members(chat_id) if member.get("player_tag")]
        lines = ["TITANI ABUSIVI - ACCOUNT REGISTRATI", ""]
        if not registered:
            lines.append("Nessun membro ha ancora collegato un account con registrami #TAG.")
            return "\n".join(lines)
        lines.append(f"Account collegati: {len(registered)}")
        for index, member in enumerate(registered, 1):
            telegram_name = member.get("display_name") or member.get("telegram_username") or "Membro"
            player_name = member.get("player_name") or "Nome non disponibile"
            tag = str(member.get("player_tag") or "").lstrip("#")
            lines.append(f"{index}. {telegram_name} → {player_name} (#{tag})")
        return "\n".join(lines)

    def _parse_dt(self, value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None

    def inactivity_rows(self, chat_id):
        settings = self.get_settings(chat_id)
        warn_days = int(settings.get("inactivity_warn_days", 5))
        kick_days = int(settings.get("inactivity_kick_days", 10))
        now = datetime.now(timezone.utc)
        rows = []
        for member in self.members(chat_id):
            last_seen = self._parse_dt(member.get("last_seen_at"))
            if not last_seen:
                continue
            vacation_until = self._parse_dt(member.get("vacation_until"))
            if vacation_until and vacation_until > now:
                continue
            days = int((now - last_seen).total_seconds() // 86400)
            if days >= warn_days:
                rows.append((member, days, days >= kick_days))
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows

    def inactivity_text(self, chat_id):
        settings = self.get_settings(chat_id)
        rows = self.inactivity_rows(chat_id)
        if not rows:
            return "Nessun membro tracciato supera attualmente la soglia di inattività."
        lines = [
            "REPORT INATTIVITA TELEGRAM",
            f"Avviso: {settings['inactivity_warn_days']} giorni | Kick: {settings['inactivity_kick_days']} giorni",
            "",
        ]
        for member, days, kick_risk in rows[:30]:
            name = member.get("display_name") or member.get("telegram_username") or str(member.get("telegram_user_id"))
            status = "DA VALUTARE PER KICK" if kick_risk else "AVVISO"
            lines.append(f"- {name}: {days} giorni - {status}")
        lines.append("Nota: il bot misura l'ultima attività vista nel gruppo, non l'ultimo accesso privato a Telegram.")
        return "\n".join(lines)

    def set_vacation(self, chat_id, user_id, days):
        until = datetime.now(timezone.utc) + timedelta(days=days)
        self._patch(
            "community_members",
            {"vacation_until": until.isoformat()},
            params={
                "chat_id": f"eq.{int(chat_id)}",
                "telegram_user_id": f"eq.{int(user_id)}",
            },
        )
        return until

    def get_settings(self, chat_id):
        defaults = {
            "chat_id": int(chat_id),
            "inactivity_warn_days": 10,
            "inactivity_kick_days": 30,
            "auto_kick": False,
            "daily_report_enabled": False,
            "weekly_report_enabled": False,
            "report_hour": 9,
            "last_daily_report_date": None,
            "last_weekly_report_key": None,
        }
        try:
            rows = self._get(
                "community_settings",
                {"select": "*", "chat_id": f"eq.{int(chat_id)}", "limit": 1},
            )
            if rows:
                defaults.update(rows[0])
        except Exception as exc:
            print("ERRORE SETTINGS:", repr(exc), flush=True)
        return defaults

    def set_settings(self, chat_id, **values):
        payload = {"chat_id": int(chat_id), **values, "updated_at": self._now_iso()}
        return self._post(
            "community_settings",
            payload,
            params={"on_conflict": "chat_id"},
            prefer="resolution=merge-duplicates,return=representation",
        )

    async def is_admin(self, context, chat_id, user_id):
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            return member.status in ("administrator", "creator")
        except Exception:
            return False

    def create_event(self, chat_id, user_id, name, when_text, mandatory=False):
        when_text = when_text.strip()
        parsed = None
        for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(when_text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError("Formato data non valido")
        local_dt = parsed.replace(tzinfo=ROME)
        utc_dt = local_dt.astimezone(timezone.utc)
        rows = self._post(
            "community_events",
            {
                "chat_id": int(chat_id),
                "name": name.strip(),
                "event_at": utc_dt.isoformat(),
                "mandatory": bool(mandatory),
                "status": "open",
                "created_by": int(user_id),
            },
        )
        return rows[0] if rows else None

    def open_events(self, chat_id):
        try:
            return self._get(
                "community_events",
                {
                    "select": "*",
                    "chat_id": f"eq.{int(chat_id)}",
                    "status": "eq.open",
                    "event_at": f"gte.{self._now_iso()}",
                    "order": "event_at.asc",
                },
            )
        except Exception as exc:
            print("ERRORE EVENTI:", repr(exc), flush=True)
            return []

    def events_text(self, chat_id):
        events = self.open_events(chat_id)
        if not events:
            return "Non ci sono eventi aperti."
        lines = ["EVENTI TITANI ABUSIVI", ""]
        for event in events[:10]:
            dt = self._parse_dt(event.get("event_at"))
            local = dt.astimezone(ROME).strftime("%d/%m/%Y %H:%M") if dt else "Data non disponibile"
            mandatory = " - OBBLIGATORIO" if event.get("mandatory") else ""
            lines.append(f"ID {event['id']} - {event['name']} - {local}{mandatory}")
        lines.append("\nPer confermare: partecipo ID")
        return "\n".join(lines)

    def confirm_event(self, event_id, user_id):
        rows = self._get("community_events", {"select": "id", "id": f"eq.{int(event_id)}", "status": "eq.open", "limit": 1})
        if not rows:
            return False
        self._post(
            "community_event_participants",
            {
                "event_id": int(event_id),
                "telegram_user_id": int(user_id),
                "status": "confirmed",
                "updated_at": self._now_iso(),
            },
            params={"on_conflict": "event_id,telegram_user_id"},
            prefer="resolution=merge-duplicates,return=minimal",
        )
        return True

    def save_recruitment(self, chat_id, user_id, username, display_name, player_tag, ranked, notes):
        return self._post(
            "community_recruitments",
            {
                "chat_id": int(chat_id),
                "telegram_user_id": int(user_id),
                "telegram_username": username,
                "display_name": display_name,
                "player_tag": player_tag.replace("#", "").upper(),
                "ranked": ranked,
                "notes": notes,
                "status": "pending",
            },
        )

    def recruitments_text(self, chat_id):
        rows = self._get(
            "community_recruitments",
            {
                "select": "*",
                "chat_id": f"eq.{int(chat_id)}",
                "status": "eq.pending",
                "order": "created_at.desc",
                "limit": 20,
            },
        )
        if not rows:
            return "Nessuna candidatura pendente."
        lines = ["CANDIDATURE PENDENTI", ""]
        for row in rows:
            name = row.get("display_name") or row.get("telegram_username") or str(row.get("telegram_user_id"))
            lines.append(
                f"- {name} | #{row.get('player_tag')} | Ranked: {row.get('ranked') or 'n/d'} | {row.get('notes') or '-'}"
            )
        return "\n".join(lines)

    async def continue_registration(self, message, context):
        stage = context.user_data.get("registration_stage")
        if not stage:
            return False

        text = (message.text or "").strip()

        if stage == "ranked_current":
            context.user_data["registration_ranked_current"] = text[:80]
            context.user_data["registration_stage"] = "ranked_peak"
            await message.reply_text("Qual è il Ranked massima che hai raggiunto?")
            return True

        if stage == "ranked_peak":
            ranked_current = context.user_data.get("registration_ranked_current", "")
            ranked_peak = text[:80]
            self.update_member_ranked(
                message.chat_id,
                message.from_user.id,
                ranked_current=ranked_current,
                ranked_peak=ranked_peak,
            )
            context.user_data.pop("registration_stage", None)
            context.user_data.pop("registration_ranked_current", None)
            await message.reply_text(
                f"Registrazione completata. Ranked attuale: {ranked_current} | Ranked massima: {ranked_peak}"
            )
            return True

        return False

    async def continue_recruitment(self, message, context):
        stage = context.user_data.get("recruitment_stage")
        if not stage:
            return False
        text = (message.text or "").strip()
        if stage == "tag":
            match = re.search(r"#?([0289PYLQGRJCUV]{3,15})", text, re.I)
            if not match:
                await message.reply_text("Tag non valido. Inviami il tuo tag Brawl Stars, per esempio #ABC123.")
                return True
            context.user_data["recruitment_tag"] = match.group(1).upper()
            context.user_data["recruitment_stage"] = "ranked"
            await message.reply_text("Qual è il tuo livello Ranked attuale?")
            return True
        if stage == "ranked":
            context.user_data["recruitment_ranked"] = text[:80]
            context.user_data["recruitment_stage"] = "notes"
            await message.reply_text("Ultima cosa: scrivi eventuali note (orari, obiettivi, esperienza) oppure rispondi 'nessuna'.")
            return True
        if stage == "notes":
            tag = context.user_data.get("recruitment_tag", "")
            ranked = context.user_data.get("recruitment_ranked", "")
            notes = "" if text.lower() == "nessuna" else text[:500]
            self.save_recruitment(
                message.chat_id,
                message.from_user.id,
                message.from_user.username,
                message.from_user.full_name,
                tag,
                ranked,
                notes,
            )
            for key in ("recruitment_stage", "recruitment_tag", "recruitment_ranked"):
                context.user_data.pop(key, None)
            await message.reply_text("Candidatura registrata. La direzione potrà consultarla dal bot.")
            return True
        return False

    def operational_report_text(self, chat_id, period="weekly"):
        period_key = str(period or "weekly").strip().casefold()
        period_days = {"daily": 0, "giornaliero": 0, "today": 0, "weekly": 7, "settimanale": 7, "7": 7, "15": 15, "30": 30}.get(period_key, 7)
        period_label = "OGGI" if period_days == 0 else f"{period_days} GIORNI"
        title = "REPORT GIORNALIERO" if period_days == 0 else f"REPORT {period_days} GIORNI"
        members = self.members(chat_id)
        ranking = self.ranking(chat_id, period_days)
        inactive = self.inactivity_rows(chat_id)
        valid_growth = [x for x in ranking if x["delta"] is not None]
        growth = sum(x["delta"] for x in valid_growth)
        lines = [
            f"TITANI ABUSIVI - {title}",
            "",
            f"Membri tracciati: {len(members)}",
            f"Giocatori registrati: {sum(1 for m in members if m.get('player_tag'))}",
            "Ambito trofei: solo utenti registrati con tag Brawl Stars collegato",
            "Ambito Progressione: solo utenti registrati con battaglie osservate valide",
            (f"Crescita trofei ({period_label.lower()}): {'+' if growth > 0 else ''}{growth}" if valid_growth else f"Crescita trofei ({period_label.lower()}): storico non ancora disponibile"),
            f"Membri sopra soglia inattività: {len(inactive)}",
        ]
        if valid_growth:
            lines.append("\n🏆 Top crescita:")
            for row in valid_growth[:5]:
                lines.append(f"- {row['name']}: {'+' if row['delta'] > 0 else ''}{row['delta']}")

        try:
            progression_members = self._get("community_members", {
                "select": "player_tag,player_name,display_name",
                "is_active": "eq.true", "player_tag": "not.is.null", "limit": "1000",
            }) or []
            progression_by_tag = {
                str(m.get("player_tag") or "").strip().lstrip("#").upper(): m
                for m in progression_members if m.get("player_tag")
            }
            progression_rows = []
            if progression_by_tag:
                response = requests.post(
                    f"{self.supabase_url}/rest/v1/rpc/coefficient_progression_rows_v2",
                    headers=self._headers(),
                    json={"p_player_tags": list(progression_by_tag), "p_days": period_days},
                    timeout=20,
                )
                response.raise_for_status()
                progression_rows = response.json() or []
            for row in progression_rows:
                member = progression_by_tag.get(str(row.get("player_tag") or "").upper(), {})
                row["_name"] = row.get("player_name") or member.get("player_name") or member.get("display_name") or row.get("player_tag")
            progression_rows = [row for row in progression_rows if row.get("progression_value") is not None and int(row.get("battle_count") or 0) > 0]
            progression_rows.sort(key=lambda row: (
                int(row.get("progression_value") or 0),
                (float(row.get("progression_value") or 0) / int(row.get("positive_trophies") or 0))
                if int(row.get("positive_trophies") or 0) > 0 else 0,
            ), reverse=True)
            if progression_rows:
                lines.append(f"\n🔥 Top Progressione ({period_label.lower()}):")
                for row in progression_rows[:5]:
                    value = int(row.get("progression_value") or 0)
                    cups = int(row.get("positive_trophies") or 0)
                    bonus = value - cups
                    coeff = (float(value) / cups) if cups > 0 else 0.0
                    lines.append(
                        f"- {row['_name']}: +{self.number_formatter(value)} "
                        f"(Coppe +{self.number_formatter(cups)} · Bonus +{self.number_formatter(bonus)} · "
                        f"Coeff. {coeff:.6f})"
                    )
        except Exception as exc:
            LOG.error("REPORT PROGRESSION ERROR: %r", exc)
            lines.append(f"\n🔥 Top Progressione ({period_label.lower()}): dati momentaneamente non disponibili")

        if inactive:
            lines.append("\nDa controllare:")
            for member, inactive_days, risk in inactive[:8]:
                name = member.get("display_name") or member.get("telegram_username") or str(member.get("telegram_user_id"))
                lines.append(f"- {name}: {inactive_days} giorni{' - RISCHIO KICK' if risk else ''}")
        return "\n".join(lines)

    def periodic_report_text(self, chat_id, scope="community", days=7, window=None, return_full=False, publish=True):
        """Combined Trophy + Progressione report. Telegram gets Top 5; Telegraph keeps the full lists."""
        days = int(days)
        if days not in (0, 7, 15, 30):
            return "I report periodici sono disponibili per 7, 15 o 30 giorni."

        scope_key = str(scope or "community").strip().casefold()
        global_scope = scope_key == "global_clubs" or scope_key.startswith("global_single:")
        allowed_clubs = {name.casefold(): name for name in self.CLUB_ALIASES.values()}
        members = []
        scope_label = "UTENTI REGISTRATI"
        scope_note = "utenti registrati con tag Brawl Stars collegato"

        if scope_key == "community":
            members = self._get("community_members", {
                "select": "player_tag,player_name,display_name,club_name",
                "is_active": "eq.true", "player_tag": "not.is.null", "limit": "1000",
            }) or []
        elif scope_key == "community_club":
            rows = self._get("community_members", {
                "select": "player_tag,player_name,display_name,club_name",
                "is_active": "eq.true", "player_tag": "not.is.null", "limit": "1000",
            }) or []
            members = [m for m in rows if str(m.get("club_name") or "").casefold() in allowed_clubs]
            scope_label = "CLUB — REGISTRATI"
            scope_note = "utenti registrati appartenenti ai 4 club ABUSIVI"
        else:
            club_key = scope_key.split(":", 1)[1] if scope_key.startswith("global_single:") else scope_key
            club_name = self.CLUB_ALIASES.get(club_key) or self.CLUB_ALIASES.get(club_key.replace(" abusivi", ""))
            if global_scope:
                wanted = [club_name] if club_name else sorted(set(self.CLUB_ALIASES.values()))
                seen = set()
                for name in wanted:
                    latest = self._get("club_roster_daily", {
                        "select": "snapshot_date", "club_name": f"eq.{name}",
                        "order": "snapshot_date.desc", "limit": "1",
                    })
                    latest_date = latest[0].get("snapshot_date") if latest else None
                    if not latest_date:
                        continue
                    for m in self._get("club_roster_daily", {
                        "select": "player_tag,player_name,club_name",
                        "club_name": f"eq.{name}", "snapshot_date": f"eq.{latest_date}", "limit": "100",
                    }) or []:
                        tag = str(m.get("player_tag") or "").lstrip("#").upper()
                        if tag and tag not in seen:
                            seen.add(tag); members.append(m)
                scope_label = f"CLUB GLOBALE — {club_name}" if club_name else "GLOBALE CLUB"
                scope_note = (f"roster completo di {club_name}" if club_name
                              else "roster completo dei 4 club ABUSIVI")
            elif club_name:
                members = self._get("community_members", {
                    "select": "player_tag,player_name,display_name,club_name",
                    "is_active": "eq.true", "player_tag": "not.is.null",
                    "club_name": f"eq.{club_name}", "limit": "1000",
                }) or []
                scope_label = club_name
                scope_note = f"utenti registrati di {club_name}"
            else:
                return "Ambito report non riconosciuto."

        by_tag = {}
        for m in members:
            tag = str(m.get("player_tag") or "").strip().lstrip("#").upper()
            if tag and tag not in by_tag:
                by_tag[tag] = m
        tags = list(by_tag)

        period_end = window[1] if window else datetime.now(timezone.utc)
        period_start = window[0] if window else (
            period_end.astimezone(ROME).replace(hour=0, minute=0, second=0, microsecond=0)
            if days == 0 else period_end - timedelta(days=days)
        )
        roster_histories = (self._roster_trophy_histories(tags, period_start, period_end)
                            if global_scope else {})
        registered_tags = set()
        if global_scope and roster_histories:
            registered_tags = {
                str(row.get("player_tag") or "").strip().lstrip("#").upper()
                for row in self._get("community_members", {
                    "select": "player_tag", "player_tag": "not.is.null", "limit": "1000",
                }) or []
            }

        def fetch_history(tag):
            # Complete rosters include many nonregistrants whose measured
            # trophy history is already in club_roster_daily. Avoid one empty
            # trophy_history HTTP request per such player and period.
            if tag in roster_histories and tag not in registered_tags:
                return []
            try:
                history_days = self._window_history_days(window[0]) if window else max(days + 2, 10)
                return self.history_fetcher(tag, days=history_days)
            except Exception as exc:
                LOG.warning("REPORT TROPHY HISTORY FAILED: tag=%s type=%s", tag, type(exc).__name__)
                return None

        # A complete club roster can exceed 100 accounts. Fetch independent
        # histories concurrently, then process them in the original tag order.
        # Keep small reports sequential to avoid thread overhead and preserve
        # their existing behavior.
        history_by_tag = None
        if len(tags) >= 16:
            with ThreadPoolExecutor(max_workers=min(6, len(tags))) as executor:
                history_by_tag = dict(zip(tags, executor.map(fetch_history, tags)))

        trophy_rows = []
        current_trophies_by_tag = {}
        for tag, m in by_tag.items():
            try:
                history = (history_by_tag[tag] if history_by_tag is not None else
                           fetch_history(tag))
                if history_by_tag is not None and history is None:
                    continue
                roster_history = roster_histories.get(tag, [])
                # Do not fabricate a full period from the first snapshot after its start.
                roster_has_baseline = any(
                    datetime.fromisoformat(row["recorded_at"]) <= period_start
                    for row in roster_history
                )
                if window:
                    baseline, current = self._window_trophy_values(history, *window)
                    if (baseline is None or current is None) and roster_has_baseline:
                        roster_baseline, roster_current = self._window_trophy_values(roster_history, *window)
                        if roster_baseline is not None and roster_current is not None:
                            baseline, current = roster_baseline, roster_current
                    if current is None:
                        _, current = self._window_trophy_values(roster_history, *window)
                    if current is None:
                        continue
                    current_trophies_by_tag[tag] = current
                    if baseline is None:
                        continue
                    trophy_rows.append({
                        "name": m.get("player_name") or m.get("display_name") or tag,
                        "tag": tag, "delta": current - baseline, "current": current,
                    })
                    continue
                current = None
                if history:
                    last = history[-1] if isinstance(history, list) else None
                    if isinstance(last, dict):
                        current = last.get("trophies")
                if current is None:
                    if roster_history:
                        current = roster_history[-1]["trophies"]
                if current is None:
                    state = self._get("player_tracking_state", {
                        "select": "trophies", "player_tag": f"eq.{tag}", "limit": "1",
                    })
                    current = state[0].get("trophies") if state else None
                if current is None:
                    continue
                current_trophies_by_tag[tag] = int(current)
                changes = self.change_calculator(history, int(current))
                delta = changes.get("today" if days == 0 else {7: "7d", 15: "15d", 30: "30d"}[days])
                if delta is None and (roster_has_baseline or days == 0):
                    roster_baseline, roster_current = self._window_trophy_values(
                        roster_history, period_start, period_end + timedelta(microseconds=1)
                    )
                    if roster_baseline is not None and roster_current is not None:
                        delta = roster_current - roster_baseline
                if delta is None:
                    continue
                trophy_rows.append({
                    "name": m.get("player_name") or m.get("display_name") or tag,
                    "tag": tag, "delta": int(delta), "current": int(current),
                })
            except Exception:
                continue
        trophy_rows.sort(key=lambda r: (r["delta"], r["current"]), reverse=True)
        # Keep all rows for club totals; hide inactive/negative players only in today's lists.
        visible_trophy_rows = [r for r in trophy_rows if r["delta"] > 0]

        progression_rows = []
        if tags:
            try:
                response = requests.post(
                    f"{self.supabase_url}/rest/v1/rpc/{'coefficient_progression_rows_range' if window else 'coefficient_progression_rows_v2'}",
                    headers=self._headers(),
                    json=({"p_player_tags": tags, "p_start": window[0].isoformat(),
                           "p_end": window[1].isoformat()} if window else
                          {"p_player_tags": tags, "p_days": days}), timeout=30,
                )
                response.raise_for_status()
                progression_rows = response.json() or []
            except Exception as exc:
                LOG.error("PERIODIC REPORT PROGRESSION ERROR: %r", exc)
        for row in progression_rows:
            m = by_tag.get(str(row.get("player_tag") or "").upper(), {})
            row["_name"] = row.get("player_name") or m.get("player_name") or m.get("display_name") or row.get("player_tag")
            row["_value"] = int(row.get("progression_value") or 0)
            row["_cups"] = int(row.get("positive_trophies") or 0)
            row["_bonus"] = row["_value"] - row["_cups"]
            row["_coeff"] = (row["_value"] / row["_cups"]) if row["_cups"] > 0 else 0.0
        progression_rows = [r for r in progression_rows if int(r.get("battle_count") or 0) > 0]
        progression_rows.sort(key=lambda r: (r["_value"], r["_coeff"]), reverse=True)
        visible_progression_rows = [r for r in progression_rows if r["_value"] > 0]

        title = f"🔥 REPORT {scope_label} — {'OGGI' if days == 0 else f'{days} GIORNI'}"
        full = [title, f"Data: {datetime.now(ROME):%d/%m/%Y %H:%M}", "", f"👥 Ambito: {scope_note}", ""]
        full.append("🏆 CLASSIFICA TROFEI")
        if visible_trophy_rows:
            for i, r in enumerate(visible_trophy_rows, 1):
                sign = "+" if r["delta"] > 0 else ""
                full.append(f"{i}. {r['name']} — {sign}{self.number_formatter(r['delta'])}")
        else:
            full.append("Nessun giocatore con crescita positiva nel periodo." if trophy_rows else
                        "Storico Trofei non ancora sufficiente per calcolare questo periodo.")
        if scope_key == "global_clubs":
            full.extend(["", f"📌 Storico Trofei disponibile: {len(trophy_rows)} su {len(tags)} giocatori del roster."])
        full.extend(["", "🔥 CLASSIFICA PROGRESSIONE"])
        if visible_progression_rows:
            for i, r in enumerate(visible_progression_rows, 1):
                full.extend([
                    f"{i}. {r['_name']}",
                    f"🎮 Partite: {int(r.get('battle_count') or 0)}",
                    f"🏆 Coppe: +{self.number_formatter(r['_cups'])}",
                    f"⚡ Bonus: +{self.number_formatter(r['_bonus'])}",
                    f"🔥 Progressione: +{self.number_formatter(r['_value'])}",
                    f"🧮 Coeff. Progressione: {r['_coeff']:.6f}".replace(".", ","),
                    "",
                ])
        else:
            full.append("Nessun giocatore con Progressione positiva nel periodo.")

        total_battles = sum(int(r.get("battle_count") or 0) for r in progression_rows)
        # Real current trophy total for the exact report scope (not period gains).
        total_real_trophies = sum(current_trophies_by_tag.values())
        total_cups = sum(r["_cups"] for r in progression_rows)
        total_progression = sum(r["_value"] for r in progression_rows)
        average_coefficient = (f"{total_progression / total_cups:.4f}".replace(".", ",")
                               if total_cups > 0 else "n.d.")
        coefficient_line = f"🧮 Coeff. medio Progressione: {average_coefficient}"
        full.extend([
            "📊 RESOCONTO",
            f"👥 Giocatori monitorati: {len(tags)}",
            f"🏆 Coppe totali reali: {self.number_formatter(total_real_trophies)}",
            f"🎮 Battaglie analizzate: {total_battles}",
            f"🏆 Coppe positive: +{self.number_formatter(total_cups)}",
            f"⚡ Bonus Progressione: +{self.number_formatter(total_progression-total_cups)}",
            f"🔥 Progressione complessiva: +{self.number_formatter(total_progression)}",
            coefficient_line,
        ])

        report_url = self._publish_telegraph(title, full) if publish else None
        summary = [title, "", "🏆 CLASSIFICA TROFEI"]
        for i, r in enumerate(visible_trophy_rows[:5], 1):
            sign = "+" if r["delta"] > 0 else ""
            summary.append(f"{i}. {r['name']} — {sign}{self.number_formatter(r['delta'])}")
        if not visible_trophy_rows:
            summary.append("Nessun giocatore con crescita positiva nel periodo." if trophy_rows else
                           "Storico Trofei non ancora sufficiente per calcolare questo periodo.")
        summary.extend([
            "", "📋 RESOCONTO",
            f"👥 Giocatori monitorati: {len(tags)}",
            f"🏆 Coppe totali reali: {self.number_formatter(total_real_trophies)}",
            f"🎮 Battaglie analizzate: {total_battles}",
            coefficient_line,
        ])
        if report_url:
            summary.extend(["", f"📊 REPORT COMPLETO: {report_url}"])
        if return_full:
            club_totals = {name: {"delta": 0, "players": 0, "roster": 0} for name in self.CLUB_TAGS}
            for member in by_tag.values():
                club = str(member.get("club_name") or "").strip().upper()
                if club in club_totals:
                    club_totals[club]["roster"] += 1
            for row in trophy_rows:
                club = str(by_tag[row["tag"]].get("club_name") or "").strip().upper()
                if club in club_totals:
                    club_totals[club]["delta"] += row["delta"]
                    club_totals[club]["players"] += 1
            return "\n".join(summary), full, club_totals
        return "\n".join(summary)

    def rankings_dashboard_text(self, chat_id, days=None):
        """Publish direct Telegraph links, reusing each period's cached snapshot."""
        if days is not None and days not in (0, 7, 15, 30):
            return "Periodo classifiche non disponibile. Usa oggi, 7, 15 o 30."
        cache_key = (int(chat_id), "shared" if days is None else f"period:{days}")
        with _DASHBOARD_CACHE_LOCK:
            cached = _DASHBOARD_CACHE.get(cache_key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
        if days is not None and getattr(self, "ready", False):
            try:
                saved = self._get("scheduled_dashboard_delivery", {
                    "select": "payload", "chat_id": f"eq.{int(chat_id)}",
                    "slot": f"eq.manual:rolling:{days}", "limit": "1",
                })
                payload = saved[0].get("payload") if saved else None
                if (isinstance(payload, dict) and payload.get("report_url")
                        and payload.get("cache_revision") == _DASHBOARD_FORMAT_REVISION):
                    stored_at = datetime.fromisoformat(str(payload.get("cached_at") or "").replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - stored_at).total_seconds()
                    max_age = 120 if days == 0 else 300
                    if 0 <= age < max_age:
                        LOG.info("CLASSIFICHE PERIOD CACHE REUSED: chat=%s days=%s age_seconds=%s", chat_id, days, int(age))
                        with _DASHBOARD_CACHE_LOCK:
                            _DASHBOARD_CACHE[cache_key] = (time.monotonic() + max_age - age, payload)
                        return payload
            except (requests.RequestException, ValueError, TypeError, KeyError, IndexError) as exc:
                LOG.warning("CLASSIFICHE PERIOD CACHE READ FAILED: %s", type(exc).__name__)
        if days is None:
            published = self._latest_published_dashboard()
            if published:
                payload, age = published
                with _DASHBOARD_CACHE_LOCK:
                    _DASHBOARD_CACHE[cache_key] = (time.monotonic() + 300 - age, payload)
                return payload
        if days is None:
            title = "Classifiche — TITANI ABUSIVI"
            now = datetime.now(ROME)
            lines = [title.upper(), f"Aggiornato: {now:%d/%m/%Y %H:%M}", "", _DASHBOARD_SOURCE_MARKER,
                     "Tre classifiche cliccabili per periodo; i Resoconti sono nel messaggio Telegram."]
            for period in (0, 7, 15, 30):
                period_payload = self.rankings_dashboard_text(chat_id, period)
                if not isinstance(period_payload, dict) or not period_payload.get("report_url"):
                    return "Dashboard Classifiche & Report temporaneamente non disponibile."
                period_lines = period_payload["fallback"].splitlines()
                start = next((i for i, row in enumerate(period_lines) if row.startswith("══ ")), None)
                if start is None:
                    raise RuntimeError("Period dashboard has no period heading")
                lines.extend(["", *period_lines[start:]])
            url = self._publish_telegraph(title, lines)
            payload = self._telegraph_reply(
                ["📊 " + title.upper(), lines[1], "", "🏆 OGGI · 7 · 15 · 30 GIORNI", "Apri l'indice per le classifiche dei quattro periodi."],
                url, lines,
            ) if url else None
        else:
            payload = self._direct_dashboard_snapshot(chat_id, days, None)
        if isinstance(payload, dict) and payload.get("report_url"):
            with _DASHBOARD_CACHE_LOCK:
                _DASHBOARD_CACHE[cache_key] = (time.monotonic() + (120 if days == 0 else 300), payload)
            if days is not None and getattr(self, "ready", False):
                try:
                    saved_payload = {**payload, "cached_at": datetime.now(timezone.utc).isoformat(),
                                     "cache_revision": _DASHBOARD_FORMAT_REVISION}
                    self._post("scheduled_dashboard_delivery", {
                        "chat_id": int(chat_id), "slot": f"manual:rolling:{days}", "payload": saved_payload,
                    }, prefer="resolution=merge-duplicates,return=minimal")
                except requests.RequestException as exc:
                    LOG.warning("CLASSIFICHE PERIOD CACHE WRITE FAILED: %s", type(exc).__name__)
        return payload or "Dashboard Classifiche & Report temporaneamente non disponibile."

    def _latest_published_dashboard(self):
        """Read the latest complete index without recomputing four periods."""
        token = os.getenv("TELEGRAPH_ACCESS_TOKEN", "").strip()
        if not token:
            return None
        try:
            for offset in range(0, 600, 200):
                response = requests.post(
                    "https://api.telegra.ph/getPageList",
                    data={"access_token": token, "offset": offset, "limit": 200}, timeout=8,
                )
                response.raise_for_status()
                result = response.json()
                if not result.get("ok"):
                    return None
                pages = result.get("result", {}).get("pages") or []
                for page in pages:
                    if str(page.get("title") or "").casefold() != "classifiche — titani abusivi":
                        continue
                    url = str(page.get("url") or "")
                    if not re.fullmatch(r"https://telegra\.ph/[^\s|<>\[\]]+", url):
                        continue
                    path = url[len("https://telegra.ph/"):]
                    detail = requests.get(
                        "https://api.telegra.ph/getPage/" + path,
                        params={"return_content": "true"}, timeout=8,
                    )
                    detail.raise_for_status()
                    page_result = detail.json()
                    if not page_result.get("ok"):
                        continue
                    nodes = page_result.get("result", {}).get("content") or []
                    values = []
                    hrefs = []
                    def visit(node):
                        if isinstance(node, str):
                            values.append(node)
                        elif isinstance(node, dict):
                            if node.get("tag") == "a":
                                hrefs.append(str((node.get("attrs") or {}).get("href") or ""))
                            for child in node.get("children") or []:
                                visit(child)
                    for node in nodes:
                        visit(node)
                    if len(hrefs) != 12 or any(not re.fullmatch(r"https://telegra\.ph/[^\s|<>\[\]]+", href) for href in hrefs):
                        continue
                    if not all(period in values for period in ("OGGI", "7 GIORNI", "15 GIORNI", "30 GIORNI")):
                        continue
                    if _DASHBOARD_SOURCE_MARKER not in values:
                        continue
                    updated = next((value for value in values if value.startswith("Aggiornato: ")), "")
                    try:
                        published_at = datetime.strptime(updated, "Aggiornato: %d/%m/%Y %H:%M").replace(tzinfo=ROME)
                    except ValueError:
                        continue
                    age = (datetime.now(ROME) - published_at).total_seconds()
                    if not 0 <= age < 300:
                        continue
                    LOG.info("CLASSIFICHE DASHBOARD REUSED: url=%s age_seconds=%s", url, int(age))
                    return self._telegraph_reply(
                        ["📊 CLASSIFICHE — TITANI ABUSIVI", updated, "", "🏆 OGGI · 7 · 15 · 30 GIORNI",
                         "Apri l'indice per le classifiche pubblicate dei quattro periodi."],
                        url, ["CLASSIFICHE — TITANI ABUSIVI", updated, url],
                    ), age
                if len(pages) < 200:
                    break
        except (requests.RequestException, ValueError, TypeError) as exc:
            LOG.warning("CLASSIFICHE DASHBOARD LOOKUP FAILED: %s", type(exc).__name__)
        return None

    def scheduled_dashboard_snapshot(self, chat_id, days, window):
        """Freeze every report page before publishing a dashboard with direct Telegraph links."""
        if days not in (0, 7, 15, 30) or not window or window[0] >= window[1]:
            raise ValueError("Invalid scheduled dashboard period")
        return self._direct_dashboard_snapshot(chat_id, days, window)

    def _direct_dashboard_snapshot(self, chat_id, days, window):
        """Create direct detail links for one period; optional fixed scheduler window."""
        label = "OGGI" if days == 0 else f"{days} GIORNI"
        title = f"Classifiche — {label}"
        lines = self._build_rankings_dashboard_text(days, publish=False, include_today_reports=True)
        links = {}
        _report, report_lines, club_totals = self.periodic_report_text(
            chat_id, "global_clubs", days, window=window, return_full=True, publish=False,
        )
        club_lines = [f"CLASSIFICA 4 CLUB — {label}",
                      "Roster completi dei quattro club ABUSIVI.", ""]
        ranked_clubs = sorted(club_totals.items(), key=lambda item: (item[1]["delta"], item[1]["players"]), reverse=True)
        measured = sum(result["players"] for result in club_totals.values())
        roster = sum(result.get("roster", result["players"]) for result in club_totals.values())
        positive_clubs = [(name, result) for name, result in ranked_clubs if result["delta"] > 0]
        for position, (name, result) in enumerate(positive_clubs, 1):
            delta = result["delta"]
            club_lines.append(f"{position}. {name} — +{self.number_formatter(delta)} "
                              f"({result['players']}/{result.get('roster', result['players'])} giocatori)")
        if not positive_clubs:
            club_lines.append("Nessun club con crescita positiva nel periodo." if measured else
                              "Storico Trofei non ancora sufficiente per calcolare questo periodo. "
                              "I roster continuano a essere censiti.")
        club_lines.extend(["", f"📌 Storico Trofei: {measured}/{roster} giocatori misurabili nel periodo."])
        links[f"dash_t_1_{days}"] = self._publish_telegraph(f"Classifica 4 Club — {label}", club_lines)
        trophy_start = report_lines.index("🏆 CLASSIFICA TROFEI")
        trophy_end = report_lines.index("🔥 CLASSIFICA PROGRESSIONE", trophy_start)
        trophy_lines = [f"CLASSIFICA TROFEI GLOBALE CLUB — {label}", report_lines[3], "",
                        *report_lines[trophy_start:trophy_end]]
        links[f"dash_t_2_{days}"] = self._publish_telegraph(f"Trofei Globali 4 Club — {label}", trophy_lines)
        progression = self.coefficient_ranking_text(chat_id, "global_clubs", days, window=window)
        links[f"dash_p_2_{days}"] = progression.get("report_url") if isinstance(progression, dict) else None
        if not links[f"dash_p_2_{days}"]:
            raise RuntimeError("The global progression page is unavailable")
        if any(not url for url in links.values()):
            raise RuntimeError("A global dashboard Telegraph page is unavailable")
        for i, line in enumerate(lines):
            match = re.fullmatch(r"\[\[DASH:(dash_[prt]_(?:[0-9]|10)_(?:0|7|15|30))\|Apri\]\]", line)
            if match:
                lines[i] = f"[[URL:{links[match.group(1)]}|Apri]]"
        if any("[[DASH:" in line for line in lines):
            raise RuntimeError("Scheduled dashboard has an unresolved link")
        if window:
            lines[1] = f"Periodo: {window[0].astimezone(ROME):%d/%m/%Y %H:%M} – {window[1].astimezone(ROME):%d/%m/%Y %H:%M}"
        url = self._publish_telegraph(title, lines)
        if not url:
            raise RuntimeError("Scheduled dashboard Telegraph page is unavailable")
        resoconto = report_lines[report_lines.index("📊 RESOCONTO") + 1:]
        return self._telegraph_reply([f"📊 {title.upper()}", lines[1], "", "📋 RESOCONTO", *resoconto], url, lines)

    def _build_rankings_dashboard_text(self, days=None, publish=True, include_today_reports=True):
        now = datetime.now(ROME)
        all_periods = [(0, "OGGI"), (7, "7 GIORNI"), (15, "15 GIORNI"), (30, "30 GIORNI")]
        periods = all_periods if days is None else [period for period in all_periods if period[0] == days]
        title = "Classifiche — TITANI ABUSIVI" if days is None else f"Classifiche — {periods[0][1]}"
        lines = [
            title.upper(),
            f"Aggiornato: {now:%d/%m/%Y %H:%M}",
            "",
            "Tre classifiche per periodo: Trofei globali, Progressione Globale Club e 4 Club. Si usano i roster completi. Il Resoconto è nel messaggio Telegram.",
        ]
        for days, label in periods:
            lines.extend(["", f"══ {label} ══", ""])
            lines.extend([
                f"🏆 Classifica Trofei Globale Club — {label}",
                "Classifica Trofei dei roster completi dei quattro club ABUSIVI.",
                f"[[DASH:dash_t_2_{days}|Apri]]", "",
                f"🔥 Progressione Globale Club — {label}",
                "Tutti i giocatori dei quattro club; Progressione calcolata battaglia per battaglia.",
                f"[[DASH:dash_p_2_{days}|Apri]]", "",
                f"🏆 Classifica dei 4 Club — {label}",
                "Confronto fra TITANI, TAMARRI, TORNADI e TALENTI con i roster completi.",
                f"[[DASH:dash_t_1_{days}|Apri]]", "",
            ])
        if not publish:
            return lines
        dashboard_url = self._publish_telegraph(title, lines)
        if not dashboard_url:
            return "Dashboard Classifiche & Report temporaneamente non disponibile."
        return self._telegraph_reply(
            ["📊 " + title.upper(), f"Aggiornato: {now:%d/%m/%Y %H:%M}", "", "Tutte le famiglie del periodo in un'unica pagina."],
            dashboard_url,
            lines,
        )

    @staticmethod
    def dashboard_command(payload):
        """Resolve only the fixed dashboard scope/period matrix."""
        match = re.fullmatch(r"dash_([prt])_(10|[0-9])_(0|7|15|30)", str(payload or ""))
        if not match:
            return None
        kind, index, period = match.group(1), int(match.group(2)), int(match.group(3))
        scopes = ("", "club", "globale club", "titani", "tamarri", "tornadi", "talenti",
                  "club globale titani", "club globale tamarri", "club globale tornadi", "club globale talenti")
        if kind == "t":
            if index > 1:
                return None
            if index:
                return f"classifica club {'oggi' if period == 0 else period}"
            return "classifica oggi" if period == 0 else f"classifica della community {period}"
        if index >= len(scopes):
            return None
        scope = scopes[index]
        if kind == "p":
            return f"classifica progressione{(' ' + scope) if scope else ''} {'oggi' if period == 0 else period}"
        return f"report{(' ' + scope) if scope else ' community'} {'oggi' if period == 0 else period}"

    async def _send_ranking_message(self, context, chat_id, text):
        """Deliver ranking replies with bounded retries on transient Telegram timeouts."""
        from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
        if isinstance(text, str) and text.lstrip().upper().startswith("CLASSIFICA"):
            full_lines = text.splitlines()
            numbered = [line for line in full_lines if re.match(r"^\d+\.\s", line.strip())]
            if numbered:
                report_url = await asyncio.to_thread(
                    self._publish_telegraph,
                    full_lines[0] if full_lines else "Classifica TITANI ABUSIVI",
                    full_lines,
                )
                if report_url:
                    first_numbered = next(
                        (index for index, line in enumerate(full_lines) if re.match(r"^\d+\.\s", line.strip())),
                        1,
                    )
                    summary = [*full_lines[:first_numbered], *numbered[:10]]
                    text = self._telegraph_reply(summary, report_url, full_lines)
                else:
                    LOG.warning("TELEGRAPH RANKING FALLBACK: chat=%s report=%s", chat_id, full_lines[0][:100])
        report_url = text.get("report_url") if isinstance(text, dict) else None
        fallback = text.get("fallback") if isinstance(text, dict) else text
        message_text = text.get("text") if isinstance(text, dict) else text
        reply_markup = None
        if report_url:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📊 REPORT COMPLETO", url=report_url)]])
        for attempt in range(3):
            try:
                await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, connect_timeout=20, read_timeout=30, write_timeout=30, pool_timeout=20)
                if report_url:
                    print("TELEGRAPH REPORT DELIVERED: chat=%s url=%s" % (chat_id, report_url), flush=True)
                elif isinstance(message_text, str) and message_text.lstrip().upper().startswith("CLASSIFICA"):
                    print("TELEGRAPH RANKING SENT WITHOUT REPORT: chat=%s" % chat_id, flush=True)
                return True
            except RetryAfter as exc:
                delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else float(exc.retry_after)
            except (TimedOut, NetworkError):
                delay = 2 ** attempt
            except TelegramError as exc:
                if report_url:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=self._telegram_fallback_links(fallback), connect_timeout=20, read_timeout=30, write_timeout=30, pool_timeout=20)
                        return True
                    except TelegramError:
                        pass
                LOG.error("RANKING TELEGRAM SEND FAILED chat=%s error=%r", chat_id, exc); return False
            LOG.warning("RANKING TELEGRAM RETRY chat=%s attempt=%s", chat_id, attempt + 1)
            if attempt < 2: await asyncio.sleep(max(1, delay))
        LOG.error("RANKING TELEGRAM SEND EXHAUSTED chat=%s", chat_id); return False

    async def handle_command(self, message, context, question):
        q_skin = question.strip()
        # Deterministic community commands must be handled before Skin/AI-like parsing.
        q0 = re.sub(r"\\s+", " ", question.strip())
        q0l = q0.casefold()
        _ranking_chat_id = message.chat_id
        if getattr(message.chat, "type", None) == "private":
            _ranking_member = context.user_data.get("_registered_user") or self.get_registered_user(message.from_user.id)
            if _ranking_member and _ranking_member.get("chat_id") is not None:
                _ranking_chat_id = int(_ranking_member["chat_id"])
        _ranking_reply_chat_id = int(message.from_user.id) if getattr(message.chat, "type", None) != "private" else int(message.chat_id)
        if q0l == "report":
            LOG.info("MANUAL REPORT FAST ROUTE chat=%s", message.chat_id)
            try:
                report_text = await asyncio.wait_for(asyncio.to_thread(self.periodic_report_text, message.chat_id, "community", 7), timeout=60)
            except asyncio.TimeoutError:
                LOG.error("MANUAL REPORT FAST ROUTE TIMEOUT chat=%s", message.chat_id)
                await message.reply_text("Il report sta impiegando troppo tempo. Riprova tra poco.")
                return True
            await message.reply_text(report_text)
            LOG.info("MANUAL REPORT FAST ROUTE DELIVERED user=%s", message.from_user.id)
            return True

        if q0l in ("come funziona il coefficiente abusivo", "guida coefficiente abusivo", "coefficiente abusivo guida"):
            guide = coefficient_guide_lines()
            report_url = await asyncio.to_thread(self._publish_telegraph, guide[0], guide)
            summary = [guide[0], "", "🏆 Ogni coppa vale almeno ×1; il calcolo è Brawler per Brawler.",
                       "📈 Progressione: battaglie osservate e punti per partita."]
            payload = self._telegraph_reply(summary, report_url, guide) if report_url else "\n".join(guide)
            await self._send_ranking_message(context, _ranking_reply_chat_id, payload)
            return True
        if q0l in ("classifica", "classifiche", "classifiche e report", "dashboard classifiche", "dashboard classifiche e report"):
            try:
                payload = await asyncio.wait_for(
                    asyncio.to_thread(self.rankings_dashboard_text, _ranking_chat_id),
                    timeout=300,
                )
            except asyncio.TimeoutError:
                LOG.error("CLASSIFICHE DASHBOARD TIMEOUT chat=%s", _ranking_chat_id)
                await message.reply_text("La dashboard Classifiche & Report sta impiegando troppo tempo. Riprova tra poco.")
                return True
            await self._send_ranking_message(context, int(message.from_user.id), payload)
            return True
        period_hub = re.fullmatch(r"(?:classifica\s+(oggi|7|15|30)|classifiche\s+(oggi|7|15|30))(?:\s+giorni)?", q0l)
        if period_hub:
            requested = period_hub.group(1) or period_hub.group(2)
            days = 0 if requested == "oggi" else int(requested)
            try:
                payload = await asyncio.wait_for(
                    asyncio.to_thread(self.rankings_dashboard_text, _ranking_chat_id, days), timeout=120,
                )
            except asyncio.TimeoutError:
                LOG.error("CLASSIFICHE PERIOD HUB TIMEOUT chat=%s days=%s", _ranking_chat_id, days)
                await message.reply_text("Le classifiche del periodo stanno impiegando troppo tempo. Riprova tra poco.")
                return True
            await self._send_ranking_message(context, int(message.from_user.id), payload)
            return True

        if q0l in ("elenco utenti", "elenco registrati", "registrati", "membri registrati", "account registrati"):
            await message.reply_text(self.registered_members_text(message.chat_id))
            return True
        if re.fullmatch(r"(?:registrami|tegistrami)\\s*#?[A-Z0-9]{3,15}", q0, re.I):
            # Registration is handled later in this method; keeping this explicit
            # guard documents that it must never fall through to the generic AI.
            pass

        async def _ranking_reply(text):
            try:
                await context.bot.send_message(chat_id=_ranking_reply_chat_id, text=text)
                return True
            except Exception as exc:
                LOG.warning("PRIVATE RANKING DELIVERY FAILED user=%s error=%r", message.from_user.id, exc)
                if _ranking_reply_chat_id != int(message.chat_id):
                    await message.reply_text(
                        "Apri prima la chat privata con Sens GPT e premi Avvia: le classifiche richieste manualmente vengono inviate solo in privato."
                    )
                    return False
                raise

        coefficient_single = re.fullmatch(r"coefficiente(?:\s+abusivo)?\s+#?([0289PYLQGRJCUV]{3,15})", q0, re.I)
        if coefficient_single:
            await message.reply_text(self.coefficient_text(coefficient_single.group(1)))
            return True

        progression_club = re.fullmatch(r"progressione\s+club(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?", q0, re.I)
        if progression_club:
            raw_period = progression_club.group(1)
            days = 0 if raw_period == "oggi" else (int(raw_period) if raw_period else None)
            await self._send_ranking_message(
                context,
                _ranking_reply_chat_id,
                self.coefficient_ranking_text(_ranking_chat_id, "community_club", days),
            )
            return True

        progression_brawler = re.fullmatch(r"progressione\s+(.+?)(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?(?:\s+#([0289PYLQGRJCUV]{3,15}))?", q0, re.I)
        if progression_brawler and progression_brawler.group(1).casefold() not in ("oggi", "7", "15", "30", "7 giorni", "15 giorni", "30 giorni"):
            brawler_name, raw_brawler_period, explicit_tag = progression_brawler.groups()
            brawler_days = 0 if raw_brawler_period in (None, "oggi") else int(raw_brawler_period)
            registered = context.user_data.get("_registered_user") or self.get_registered_user(message.from_user.id)
            detail_tag = explicit_tag or (registered or {}).get("player_tag")
            if not detail_tag:
                await message.reply_text("Devi essere registrato oppure usare: progressione NOME_BRAWLER #TAG.")
                return True
            await self._send_ranking_message(
                context, message.chat_id, self.progression_brawler_text(detail_tag, brawler_name, brawler_days)
            )
            return True

        progression_detail = re.fullmatch(r"progressione(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?(?:\s+#?([0289PYLQGRJCUV]{3,15}))?", q0, re.I)
        if progression_detail:
            raw_period, explicit_tag = progression_detail.groups()
            detail_days = 0 if raw_period in (None, "oggi") else int(raw_period)
            registered = context.user_data.get("_registered_user") or self.get_registered_user(message.from_user.id)
            detail_tag = explicit_tag or (registered or {}).get("player_tag")
            if not detail_tag:
                await message.reply_text("Devi essere registrato oppure usare: progressione oggi #TAG.")
                return True
            await self._send_ranking_message(context, _ranking_reply_chat_id, self.progression_detail_text(detail_tag, detail_days))
            return True

        # Coefficiente Abusivo progression family. Keep all supported forms here
        # so every recognized command is routed deterministically and never falls
        # through to the AI conversation handler.
        progression_patterns = (
            (r"classifica\s+progressione(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?", "community"),
            (r"classifica\s+progressione\s+community(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?", "community"),
            (r"classifica\s+progressione\s+club(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?", "community_club"),
            (r"classifica\s+progressione\s+(?:globale\s+club|club\s+globale)(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?", "global_clubs"),
            (r"classifica\s+progressione\s+(titani|tamarri|tornadi|talenti)(?:\s+abusivi)?(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?", "registered_club"),
            (r"classifica\s+progressione\s+club\s+globale\s+(titani|tamarri|tornadi|talenti)(?:\s+abusivi)?(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?", "global_single_club"),
        )
        for progression_pattern, progression_scope in progression_patterns:
            progression_match = re.fullmatch(progression_pattern, q0, re.I)
            if not progression_match:
                continue
            groups = progression_match.groups()
            if progression_scope in ("registered_club", "global_single_club"):
                club_key = groups[0].lower()
                raw_period = groups[1]
                scope = ("global_single:" + club_key) if progression_scope == "global_single_club" else club_key
            else:
                raw_period = groups[0] if groups else None
                scope = progression_scope
            days = 0 if raw_period == "oggi" else (int(raw_period) if raw_period else None)
            await self._send_ranking_message(
                context,
                _ranking_reply_chat_id,
                self.coefficient_ranking_text(_ranking_chat_id, scope, days),
            )
            return True

        # Trophy leaderboard commands are common and can be expensive: route them
        # before Skin Account/user registration lookups so they cannot be delayed
        # by unrelated per-user database work.
        # Keep club-vs-club daily ranking distinct from the individual daily ranking.
        # Both are deterministic and must never fall through to Gemini.
        # "oggi" is optional in the natural manual forms.
        # Route the more specific club command first.
        if re.fullmatch(r"classifica\s+globale\s+club(?:\s+(?:di\s+)?oggi)?", q0, re.I):
            await self._send_ranking_message(context, _ranking_reply_chat_id, self.global_club_ranking_text(_ranking_chat_id, monthly=False))
            return True
        if re.fullmatch(r"classifica\s+globale(?:\s+(?:di\s+)?oggi)?", q0, re.I):
            await context.bot.send_message(
                chat_id=_ranking_reply_chat_id,
                text=self.global_ranking_text(_ranking_chat_id, 0),
            )
            return True
        if re.fullmatch(r"classifica\s+globale\s+mensile", q0, re.I):
            await self._send_ranking_message(context, _ranking_reply_chat_id, self.global_monthly_ranking_text(_ranking_chat_id))
            return True
        if re.fullmatch(r"classifica\s+globale\s+club\s+mensile", q0, re.I):
            await self._send_ranking_message(context, _ranking_reply_chat_id, self.global_club_ranking_text(_ranking_chat_id, monthly=True))
            return True
        if re.fullmatch(r"classifica\s+(?:dei\s+)?club\s+(?:di\s+)?oggi", q0, re.I):
            await context.bot.send_message(
                chat_id=_ranking_reply_chat_id,
                text=self.club_trophy_ranking_text(_ranking_chat_id, 0),
            )
            return True
        club_period = re.fullmatch(r"classifica\s+club\s+(7|15|30)(?:\s+giorni)?", q0, re.I)
        if club_period:
            await self._send_ranking_message(
                context, _ranking_reply_chat_id,
                self.club_trophy_ranking_text(_ranking_chat_id, int(club_period.group(1))),
            )
            return True
        if re.fullmatch(r"classifica(?:\s+(?:della\s+community))?(?:\s+di)?\s+oggi", q0, re.I):
            await self._send_ranking_message(context, _ranking_reply_chat_id, self.ranking_text(_ranking_chat_id, 0))
            return True
        _club_default_fast = re.fullmatch(
            r"classific(?:a|he)\\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)",
            q0, re.I,
        )
        if _club_default_fast:
            _club_name = self.CLUB_ALIASES[_club_default_fast.group(1).lower()]
            await context.bot.send_message(chat_id=_ranking_reply_chat_id, text=self.stat_ranking_text(_ranking_chat_id, "trofei", _club_name))
            return True

        registered = context.user_data.get("_registered_user") or self.get_registered_user(message.from_user.id)

        # Skin Account: totals, category queries, owned/missing lists and per-Brawler details.
        category_rx = r"(rare|super\s+rare|epiche|mitiche|leggendarie|skin\s+overdrive\s+pass\s+pro|(?:skin\s+)?overdrive|ipercharge|collezione|collector|pass\s+pro|brawl\s+pass|argento|oro(?:\s+24\s+carati)?|speciali|senza\s+rarit[àa])"
        rarity_aliases = {
            "rare":"Rare", "super rare":"Super rare", "epiche":"Epiche", "mitiche":"Mitiche",
            "leggendarie":"Leggendarie", "overdrive":"Skin Overdrive", "skin overdrive":"Skin Overdrive",
            "ipercharge":"Skin Overdrive", "collezione":"Collezione", "collector":"Collezione",
            "skin overdrive pass pro":"Skin Overdrive Pass Pro", "pass pro":"Pass Pro", "brawl pass":"Brawl Pass", "argento":"Argento",
            "oro":"Oro", "oro 24 carati":"Oro", "speciali":"Senza rarità",
            "senza rarità":"Senza rarità", "senza rarita":"Senza rarità",
        }
        cat = lambda raw: rarity_aliases[re.sub(r"\s+", " ", raw.lower()).strip()]

        skin_chart_q = re.fullmatch(r"(?:fammi\s+)?grafico\s+skin(?:\s+"+category_rx+r")?(?:\s+(7|15|30|60|90|180|365)(?:\s+giorni)?)?", q_skin, re.I)
        if skin_chart_q:
            chart_category = cat(skin_chart_q.group(1)) if skin_chart_q.group(1) else "Totale"
            chart_days = int(skin_chart_q.group(2) or 30)
            chart, error = self.skin_history_chart(registered, category=chart_category, days=chart_days)
            if error:
                await message.reply_text(error)
            else:
                await message.reply_photo(photo=chart, caption=f"Skin Account — {chart_category} — ultimi {chart_days} giorni")
            return True

        skin_image_q = re.fullmatch(
            r"(?:mostrami|fammi\s+vedere|immagine(?:\s+di)?|foto(?:\s+di)?)\s+(?:la\s+skin\s+)?(.+?)\s+(?:di|del|della)\s+(.+)",
            q_skin, re.I,
        )
        category_brawler_list_q = re.fullmatch(r"quali\s+skin\s+"+category_rx+r"\s+(?:di|del|della)\s+(.+?)\s+(mi\s+mancano|ho|possiedo)", q_skin, re.I)
        category_list_q = re.fullmatch(r"quali\s+skin\s+"+category_rx+r"\s+(mi\s+mancano|ho|possiedo)", q_skin, re.I)
        category_brawler_count_q = re.fullmatch(r"quante\s+skin\s+"+category_rx+r"\s+(?:ha|di|del|della)\s+(.+)", q_skin, re.I)
        category_count_q = re.fullmatch(r"quante\s+skin\s+"+category_rx+r"(?:\s+(?:ho|possiedo))?", q_skin, re.I)
        missing_brawler_q = re.fullmatch(r"(?:quali\s+)?skin\s+(?:di|del|della)\s+(.+?)\s+(?:mi\s+)?mancano", q_skin, re.I) or re.fullmatch(r"(?:quali\s+)?skin\s+(?:mi\s+)?mancano\s+(?:di|del|della)\s+(.+)", q_skin, re.I)
        owned_brawler_q = re.fullmatch(r"(?:quali\s+)?skin\s+(?:di|del|della)\s+(.+?)\s+(?:ho|possiedo)", q_skin, re.I)
        account_brawler_q = re.fullmatch(r"(?:fammi\s+)?skin\s+account\s+(?:di\s+)?(.+)", q_skin, re.I)
        simple_skin_brawler_q = re.fullmatch(r"skin\s+(.+?)(?:\s+(possedute|posseduti|ho|mancanti|mancano))?", q_skin, re.I)
        skin_brawler_count_q = re.fullmatch(r"quante\s+skin\s+(?:ho\s+)?(?:di|del|della)\s+(.+)", q_skin, re.I) or re.fullmatch(r"quante\s+skin\s+ha\s+(.+)", q_skin, re.I)
        skin_all_q = re.fullmatch(r"(?:quante\s+)?skin(?:\s+(?:ho|possiedo))?", q_skin, re.I)

        answer = None
        # Conversational Skin Account follow-up: remember the last Brawler scope.
        # Example: "Quante skin ho di Moe?" -> "Quali ho?" / "Quali mi mancano?"
        last_skin = context.user_data.get("skin_account_context") or {}
        follow_owned = re.fullmatch(r"(?:quali(?:\s+skin)?\s+)?(?:ho|possiedo|ho io)", q_skin, re.I) or re.fullmatch(r"quali\s+ho", q_skin, re.I)
        follow_missing = re.fullmatch(r"(?:quali(?:\s+skin)?\s+)?(?:mi\s+mancano|mancano)", q_skin, re.I) or re.fullmatch(r"quali\s+mi\s+mancano", q_skin, re.I)
        if (follow_owned or follow_missing) and last_skin.get("brawler"):
            answer = self.skin_account_text(
                registered,
                brawler_name=last_skin["brawler"],
                category=last_skin.get("category"),
                mode="owned" if follow_owned else "missing",
            )
        elif skin_image_q:
            result = await self.send_skin_image(message, skin_image_q.group(2).strip(), skin_image_q.group(1).strip())
            if result is not True:
                await message.reply_text(result)
            return True
        if category_brawler_list_q:
            mode = "missing" if "mancano" in category_brawler_list_q.group(3).lower() else "owned"
            answer = self.skin_account_text(registered, brawler_name=category_brawler_list_q.group(2).strip(), category=cat(category_brawler_list_q.group(1)), mode=mode)
        elif category_list_q:
            mode = "missing" if "mancano" in category_list_q.group(2).lower() else "owned"
            answer = self.skin_account_text(registered, category=cat(category_list_q.group(1)), mode=mode)
        elif category_brawler_count_q:
            answer = self.skin_account_text(registered, brawler_name=category_brawler_count_q.group(2).strip(), category=cat(category_brawler_count_q.group(1)), mode="count")
        elif category_count_q:
            answer = self.skin_account_text(registered, category=cat(category_count_q.group(1)), mode="count")
        elif missing_brawler_q:
            answer = self.skin_account_text(registered, brawler_name=missing_brawler_q.group(1).strip(), mode="missing")
        elif owned_brawler_q:
            answer = self.skin_account_text(registered, brawler_name=owned_brawler_q.group(1).strip(), mode="owned")
        elif account_brawler_q:
            answer = self.skin_account_text(registered, brawler_name=account_brawler_q.group(1).strip(), mode="full")
        elif simple_skin_brawler_q and self._skin_key(simple_skin_brawler_q.group(1)) not in ("account",):
            _skin_mode = (simple_skin_brawler_q.group(2) or "").lower()
            _mode = "missing" if _skin_mode in ("mancanti","mancano") else ("owned" if _skin_mode in ("possedute","posseduti","ho") else "full")
            answer = self.skin_account_text(registered, brawler_name=simple_skin_brawler_q.group(1).strip(), mode=_mode)
        elif skin_brawler_count_q:
            answer = self.skin_account_text(registered, brawler_name=skin_brawler_count_q.group(1).strip(), mode="count")
        elif skin_all_q:
            answer = self.skin_account_text(registered)
        if answer is not None:
            scoped_brawler = None
            scoped_category = None
            if category_brawler_list_q:
                scoped_brawler = category_brawler_list_q.group(2).strip(); scoped_category = cat(category_brawler_list_q.group(1))
            elif category_brawler_count_q:
                scoped_brawler = category_brawler_count_q.group(2).strip(); scoped_category = cat(category_brawler_count_q.group(1))
            elif missing_brawler_q:
                scoped_brawler = missing_brawler_q.group(1).strip()
            elif owned_brawler_q:
                scoped_brawler = owned_brawler_q.group(1).strip()
            elif account_brawler_q:
                scoped_brawler = account_brawler_q.group(1).strip()
            elif simple_skin_brawler_q and self._skin_key(simple_skin_brawler_q.group(1)) != "account":
                scoped_brawler = simple_skin_brawler_q.group(1).strip()
            elif skin_brawler_count_q:
                scoped_brawler = skin_brawler_count_q.group(1).strip()
            if scoped_brawler:
                context.user_data["skin_account_context"] = {"brawler": scoped_brawler, "category": scoped_category}
            await self.send_skin_telegraph(context, int(message.from_user.id), answer)
            return True

        q = (question or "").strip()
        q = re.sub(r"^[!/]+", "", q).strip()
        q = q.strip("\"\'“”‘’ ").strip()
        ql = q.lower()

        if ql in ("novita memoria", "novità memoria", "comunicazione memoria", "telegraph memoria"):
            # Internal publishing trigger: keep it out of the normal manual
            # command/private-delivery family. It exists only to create the
            # official Telegraph page through the runtime token.
            announcement_lines = MEMORY_ANNOUNCEMENT_TEXT.splitlines()
            announcement_url = self._publish_telegraph("Novità Sens GPT — Memoria personale", announcement_lines)
            if announcement_url:
                await self._send_ranking_message(context, _ranking_reply_chat_id, self._telegraph_reply(
                    ["🧠 NOVITÀ SENS GPT — MEMORIA PERSONALE", "Apri la comunicazione completa."],
                    announcement_url, announcement_lines
                ))
            else:
                await message.reply_text(MEMORY_ANNOUNCEMENT_TEXT)
            return True

        if ql in ("guida progressione", "progressione guida"):
            guide_lines = PROGRESSION_GUIDE_TEXT.splitlines()
            guide_url = self._publish_telegraph("Guida Progressione — TITANI ABUSIVI", guide_lines)
            if guide_url:
                await self._send_ranking_message(context, _ranking_reply_chat_id, self._telegraph_reply(
                    [
                        "🔥 GUIDA PROGRESSIONE — TITANI ABUSIVI",
                        "",
                        "🏆 Scopri come viene calcolata la tua Progressione, Brawler per Brawler e battaglia dopo battaglia.",
                        "",
                        "⚡ Trovi Coppe, Bonus, Coeff. Progressione, periodi Oggi/7/15/30, singolo Brawler e classifiche.",
                        "",
                        "📖 Apri la Guida Progressione completa",
                    ],
                    guide_url, guide_lines
                ))
            else:
                await message.reply_text(PROGRESSION_GUIDE_TEXT)
            return True

        if ql in ("aiuto", "help", "comandi", "funzioni"):
            guide = await asyncio.to_thread(self._publish_command_guide)
            if guide:
                await self._send_ranking_message(context, _ranking_reply_chat_id, guide)
            else:
                await message.reply_text("La guida Telegraph dei comandi non è disponibile in questo momento. Riprova tra poco.")
            return True

        if ql in ("regole", "faq", "regolamento"):
            await message.reply_text(FAQ_TEXT)
            return True

        draft_start = re.fullmatch(r"(?:draft\s+ranked|ranked\s+draft|classificata\s+draft|draft\s+classificata)", q, re.I)
        if draft_start:
            if getattr(message.chat, "type", None) != "private":
                await message.reply_text(
                    "La Draft Ranked funziona solo nella chat privata con Sens GPT."
                )
                return True
            context.user_data.pop("ranked_draft", None)
            context.user_data.pop("ranked_draft_elo", None)
            context.user_data["ranked_draft_setup"] = {"stage": "map"}
            await message.reply_text("DRAFT RANKED — Inserisci la mappa.")
            return True

        setup = context.user_data.get("ranked_draft_setup") or {}
        if setup.get("stage") == "map_choice":
            choices=setup.get("map_choices") or []
            chosen=None
            raw_choice=q.strip()
            if re.fullmatch(r"[1-9]\\d*",raw_choice):
                idx=int(raw_choice)-1
                if 0<=idx<len(choices): chosen=choices[idx]
            if chosen is None:
                for item in choices:
                    if raw_choice.casefold() in {str(item.get("map_en") or "").casefold(),str(item.get("map_it") or "").casefold()}:
                        chosen=item;break
            if chosen is None:
                await message.reply_text("Scelta non riconosciuta. Rispondi con il numero oppure con il nome della mappa.")
                return True
            setup.update({"stage":"rank","map":chosen.get("map_en"),"map_it":chosen.get("map_it"),"map_id":chosen.get("map_id"),"mode":chosen.get("mode_en"),"mode_it":chosen.get("mode_it"),"mode_api":chosen.get("mode_api")})
            context.user_data["ranked_draft_setup"]=setup
            if setup.get("pending_rank"):
                q=setup.pop("pending_rank")
            else:
                me=context.user_data.get("_registered_user") or {}
                current=me.get("ranked_current")
                if current not in (None,"Non classificato","Unranked"):
                    q=str(current)
                else:
                    await message.reply_text(f"Mappa: {chosen.get('map_it') or chosen.get('map_en')}.\\nIndica il Ranked da simulare, per esempio 'Mito I' o 'Mito 2'.")
                    return True
        if setup.get("stage") == "map":
            combined_q = re.sub(r"\b(?:miti|mitico|mitico|mythic)(?=\s+(?:i{1,3}|[1-3])\b)", "mito", q, flags=re.I)
            combined_q = re.sub(r"\bdiamnte(?=\s+(?:i{1,3}|[1-3])\b)", "diamante", combined_q, flags=re.I)
            combined_q = re.sub(r"\bleggendrio(?=\s+(?:i{1,3}|[1-3])\b)", "leggendario", combined_q, flags=re.I)
            combined = re.fullmatch(r"(.+?)\s+(bronzo|argento|oro|diamante|mito|mitico|mythic|leggendario)\s+(i{1,3}|[1-3])$", combined_q, re.I)
            pending_rank = None
            map_query = q
            if combined:
                map_query = combined.group(1).strip()
                level = combined.group(3).upper()
                level = {"1":"I","2":"II","3":"III"}.get(level, level)
                rank_base=combined.group(2).casefold()
                rank_base={"mitico":"mito","mythic":"mito"}.get(rank_base,rank_base)
                pending_rank = f"{rank_base.title()} {level}"
            # The user may enter a Ranked mode instead of a map. Show the
            # current seasonal maps for that mode and accept number or map name.
            mode_input=map_query.casefold().strip()
            mode_lookup={"footbrawl":"brawlBall","brawl ball":"brawlBall","brawlball":"brawlBall",
                         "arraffagemme":"gemGrab","gem grab":"gemGrab","gemgrab":"gemGrab",
                         "dominio":"hotZone","hot zone":"hotZone","hotzone":"hotZone","zona rovente":"hotZone",
                         "ricercati":"bounty","bounty":"bounty","rapina":"heist","heist":"heist",
                         "k.o.":"knockout","ko":"knockout","knockout":"knockout"}
            mode_api=mode_lookup.get(mode_input)
            if mode_api:
                from ranked_matchup_sync import current_ranked_pool
                pool=current_ranked_pool()
                choices=[]
                for pool_mode,pool_map in sorted(pool,key=lambda x:x[1].casefold()):
                    if pool_mode!=mode_api: continue
                    ident=self._draft_identity(pool_map)
                    if ident: choices.append(ident)
                if not choices:
                    await message.reply_text("Nessuna mappa Ranked corrente disponibile per questa modalità.")
                    return True
                setup.update({"stage":"map_choice","map_choices":choices,"pending_rank":pending_rank})
                context.user_data["ranked_draft_setup"]=setup
                mode_label=choices[0].get("mode_it") or choices[0].get("mode_en") or map_query
                lines=[f"{str(mode_label).upper()} — MAPPE RANKED"]
                if pending_rank: lines.append(f"Fascia Ranked: {pending_rank}")
                lines.extend(f"{i}. {x.get('map_it') or x.get('map_en')}" for i,x in enumerate(choices,1))
                lines.append("Rispondi con il numero oppure con il nome della mappa.")
                await message.reply_text("\\n".join(lines))
                return True
            identity = self._draft_identity(map_query)
            if not identity:
                await message.reply_text("Mappa non presente in Ranked.")
                return True
            setup.update({"stage": "rank", "map": identity.get("map_en"), "map_it": identity.get("map_it"), "map_id": identity.get("map_id"), "mode": identity.get("mode_en"), "mode_it": identity.get("mode_it"), "mode_api": identity.get("mode_api")})
            context.user_data["ranked_draft_setup"] = setup
            me = context.user_data.get("_registered_user") or {}
            current = me.get("ranked_current")
            if pending_rank:
                q = pending_rank
            elif current not in (None, "Non classificato", "Unranked"):
                q = str(current)
            else:
                await message.reply_text(f"Mappa: {identity.get('map_it') or identity.get('map_en')}.\nRanked attuale non disponibile. Indica il Ranked da simulare, per esempio 'Mito I' o 'Mito 2'.")
                return True
        if setup.get("stage") == "rank":
            rank_aliases_setup = {
                "bronzo i":"Bronzo I","bronzo ii":"Bronzo II","bronzo iii":"Bronzo III",
                "argento i":"Argento I","argento ii":"Argento II","argento iii":"Argento III",
                "oro i":"Oro I","oro ii":"Oro II","oro iii":"Oro III",
                "diamante i":"Diamante I","diamante ii":"Diamante II","diamante iii":"Diamante III",
                "mito i":"Mito I","mito ii":"Mito II","mito iii":"Mito III",
                "leggendario i":"Leggendario I","leggendario ii":"Leggendario II","leggendario iii":"Leggendario III",
                "maestro":"Maestro",
            }
            rank_aliases_setup.update({
                "bronzo 1":"Bronzo I","bronzo 2":"Bronzo II","bronzo 3":"Bronzo III",
                "argento 1":"Argento I","argento 2":"Argento II","argento 3":"Argento III",
                "oro 1":"Oro I","oro 2":"Oro II","oro 3":"Oro III",
                "diamante 1":"Diamante I","diamante 2":"Diamante II","diamante 3":"Diamante III",
                "mito 1":"Mito I","mito 2":"Mito II","mito 3":"Mito III",
                "leggendario 1":"Leggendario I","leggendario 2":"Leggendario II","leggendario 3":"Leggendario III",
            })
            raw_rank = re.sub(r"\s+", " ", q.casefold()).strip()
            raw_rank = re.sub(r"^(?:miti|mitico|mitico|mythic)(?=\s)", "mito", raw_rank)
            raw_rank = re.sub(r"^diamnte(?=\s)", "diamante", raw_rank)
            raw_rank = re.sub(r"^legg(?:end)?rio(?=\s)", "leggendario", raw_rank)
            me = context.user_data.get("_registered_user") or {}
            if raw_rank in ("usa il mio ranked","mio ranked","ranked attuale","usa ranked attuale"):
                rank_name = me.get("ranked_current")
                elo = me.get("ranked_current_elo")
            else:
                rank_name = rank_aliases_setup.get(raw_rank)
                elo = None
            if not rank_name or rank_name in ("Non classificato","Unranked"):
                await message.reply_text("Ranked non riconosciuto. Scrivi per esempio 'Mito I' oppure 'usa il mio ranked'.")
                return True
            map_name = setup.get("map")
            try:
                response = self.draft_map_advice_text(map_name, rank_name=rank_name)
            except Exception as exc:
                LOG.warning("DRAFT advice unavailable map=%s error=%s", map_name, type(exc).__name__)
                response = None
            if not response:
                map_label = setup.get("map_it") or setup.get("map") or map_name
                response = f"RANKED - {str(map_label).upper()}\nFascia Ranked: {rank_name}"
            draft_format = self._ranked_draft_format(rank_name)
            context.user_data["ranked_draft"] = {"map": setup.get("map"), "map_it": setup.get("map_it"), "map_id": setup.get("map_id"), "mode": setup.get("mode"), "mode_it": setup.get("mode_it"), "mode_api": setup.get("mode_api"), "rank": rank_name, "elo": elo, "draft_format": draft_format, "first_pick": None, "pick_sequence": [], "my_picks": [], "enemy_picks": [], "bans": []}
            context.user_data.pop("ranked_draft_setup", None)
            if draft_format == "all_pick":
                response += "\nFormato: selezione normale, senza ban. Puoi iniziare con i pick."
            elif draft_format == "ban_all_pick":
                response += "\nFormato: 6 ban totali (3+3). Inizia con i ban; i pick si aprono dopo il sesto ban."
            elif draft_format == "turn_pick":
                response += "\nFormato: 6 ban totali (3+3), poi pick a turni 1-2-2-1. Inizia con i ban."
            await message.reply_text(response)
            return True

        ranked_map = re.fullmatch(r"(?:draft\s+ranked|ranked|classificata)\s+(.+)", q, re.I)
        if ranked_map and not re.fullmatch(r"(?:oggi|7|15|30)(?:\s+giorni)?", ranked_map.group(1), re.I):
            if getattr(message.chat, "type", None) != "private":
                await message.reply_text(
                    "La Draft Ranked funziona solo nella chat privata con Sens GPT."
                )
                return True
            raw = ranked_map.group(1).strip()
            rank_aliases = {
                "bronzo i":"Bronzo I","bronzo 1":"Bronzo I","bronze i":"Bronzo I","bronze 1":"Bronzo I",
                "bronzo ii":"Bronzo II","bronzo 2":"Bronzo II","bronze ii":"Bronzo II","bronze 2":"Bronzo II",
                "bronzo iii":"Bronzo III","bronzo 3":"Bronzo III","bronze iii":"Bronzo III","bronze 3":"Bronzo III",
                "argento i":"Argento I","argento 1":"Argento I","silver i":"Argento I","silver 1":"Argento I",
                "argento ii":"Argento II","argento 2":"Argento II","silver ii":"Argento II","silver 2":"Argento II",
                "argento iii":"Argento III","argento 3":"Argento III","silver iii":"Argento III","silver 3":"Argento III",
                "oro i":"Oro I","oro 1":"Oro I","gold i":"Oro I","gold 1":"Oro I",
                "oro ii":"Oro II","oro 2":"Oro II","gold ii":"Oro II","gold 2":"Oro II",
                "oro iii":"Oro III","oro 3":"Oro III","gold iii":"Oro III","gold 3":"Oro III",
                "diamante i":"Diamante I","diamante 1":"Diamante I","diamond i":"Diamante I","diamond 1":"Diamante I",
                "diamante ii":"Diamante II","diamante 2":"Diamante II","diamond ii":"Diamante II","diamond 2":"Diamante II",
                "diamante iii":"Diamante III","diamante 3":"Diamante III","diamond iii":"Diamante III","diamond 3":"Diamante III",
                "mito i":"Mito I","mito 1":"Mito I","mythic i":"Mito I","mythic 1":"Mito I",
                "mito ii":"Mito II","mito 2":"Mito II","mythic ii":"Mito II","mythic 2":"Mito II",
                "mito iii":"Mito III","mito 3":"Mito III","mythic iii":"Mito III","mythic 3":"Mito III",
                "leggendario i":"Leggendario I","leggendario 1":"Leggendario I","legendary i":"Leggendario I","legendary 1":"Leggendario I",
                "leggendario ii":"Leggendario II","leggendario 2":"Leggendario II","legendary ii":"Leggendario II","legendary 2":"Leggendario II",
                "leggendario iii":"Leggendario III","leggendario 3":"Leggendario III","legendary iii":"Leggendario III","legendary 3":"Leggendario III",
                "maestro":"Maestro","masters":"Maestro","master":"Maestro",
            }
            rank_name = None
            map_name = raw
            for alias, canonical in sorted(rank_aliases.items(), key=lambda x: len(x[0]), reverse=True):
                if raw.casefold().endswith(" "+alias) or raw.casefold() == alias:
                    rank_name=canonical
                    map_name=raw[:-len(alias)].strip()
                    break
            if not rank_name:
                # answer() resolves the registered Telegram identity once per request.
                # Reuse it here instead of performing a second community_members query.
                me=context.user_data.get("_registered_user") or {}
                current=me.get("ranked_current")
                elo=me.get("ranked_current_elo")
                if current not in (None,"Non classificato","Unranked"):
                    rank_name=current
                    try:
                        context.user_data["ranked_draft_elo"]=int(elo) if elo is not None else None
                    except (TypeError,ValueError):
                        context.user_data["ranked_draft_elo"]=None
            response = self.draft_map_advice_text(map_name, rank_name=rank_name)
            if response:
                identity=self._draft_identity(map_name)
                draft_format=self._ranked_draft_format(rank_name)
                context.user_data["ranked_draft"] = {"map": identity.get("map_en") if identity else map_name, "map_it": identity.get("map_it") if identity else map_name, "map_id": identity.get("map_id") if identity else None, "mode": identity.get("mode_en") if identity else None, "mode_it": identity.get("mode_it") if identity else None, "mode_api": identity.get("mode_api") if identity else None, "rank": rank_name, "elo": context.user_data.pop("ranked_draft_elo", None), "draft_format": draft_format, "first_pick": None, "pick_sequence": [], "my_picks": [], "enemy_picks": [], "bans": []}
                if draft_format == "all_pick":
                    response += "\nFormato: selezione normale, senza ban."
                elif draft_format == "ban_all_pick":
                    response += "\nFormato: 6 ban totali (3+3), poi selezione normale senza ordine a turni."
                elif draft_format == "turn_pick":
                    response += "\nFormato: 6 ban totali (3+3), poi 6 pick con ordine 1-2-2-1.\nIndica: primo pick nostro oppure primo pick avversario."
                await message.reply_text(response)
                return True

        draft_state = context.user_data.get("ranked_draft") or {}
        if draft_state and re.fullmatch(r"(?:stato|riepilogo|mostra)\\s+(?:draft|classificata)|draft\\s+(?:stato|riepilogo)", q, re.I):
            lines=[f"DRAFT — {draft_state.get('map_it') or draft_state.get('map') or 'mappa'}"]
            if draft_state.get("mode_it"): lines.append("Modalità: "+str(draft_state["mode_it"]))
            if draft_state.get("rank"):
                rank_line="Ranked: "+str(draft_state["rank"])
                if draft_state.get("elo") is not None: rank_line+=f" — ELO {draft_state['elo']}"
                lines.append(rank_line)
            lines.append("Miei pick: "+(", ".join(draft_state.get("my_picks") or []) or "nessuno"))
            lines.append("Pick avversari: "+(", ".join(draft_state.get("enemy_picks") or []) or "nessuno"))
            lines.append("Ban: "+(", ".join(draft_state.get("bans") or []) or "nessuno"))
            if draft_state.get("draft_format") == "turn_pick":
                turn=self._draft_next_turn_text(draft_state)
                if turn: lines.append(turn)
            comp_advice=self.draft_comp_advice_text(draft_state)
            if comp_advice: lines.append(comp_advice)
            await message.reply_text("\\n".join(lines))
            return True
        if draft_state and re.fullmatch(r"(?:reset|azzera|annulla|chiudi)\\s+(?:draft|classificata)|(?:draft|classificata)\\s+(?:reset|azzera|annulla|chiudi)", q, re.I):
            context.user_data.pop("ranked_draft",None)
            context.user_data.pop("ranked_draft_elo",None)
            context.user_data.pop("ranked_draft_setup",None)
            await message.reply_text("Draft chiusa. Puoi iniziarne una nuova con: Draft Ranked.")
            return True
        first_pick_q = re.fullmatch(r"(?:(?:(?:primo|first)\s+pick|pick)\s+(nostro|mio|squadra|avversario|avversaria|nemico)|(nostro|mio|squadra|avversario|avversaria|nemico)\s+pick)", q, re.I)
        if draft_state and first_pick_q:
            if draft_state.get("draft_format") != "turn_pick":
                await message.reply_text("L'ordine a turni dei pick si usa da Mito I in poi.")
                return True
            if len(draft_state.get("bans") or []) < 6:
                await message.reply_text("Prima completa i 6 ban. Poi indica: pick nostro oppure pick avversario.")
                return True
            raw_side=(first_pick_q.group(1) or first_pick_q.group(2)).casefold()
            draft_state["first_pick"]="enemy" if raw_side in ("avversario","avversaria","nemico") else "my"
            draft_state["pick_sequence"]=[]
            draft_state["my_picks"]=[]
            draft_state["enemy_picks"]=[]
            context.user_data["ranked_draft"]=draft_state
            body="Ordine pick impostato. "+self._draft_next_turn_text(draft_state)
            # If our side owns pick 1, recommend immediately: waiting for a pick
            # would make the advice arrive one turn too late.
            if draft_state["first_pick"] == "my":
                excluded=(draft_state.get("bans") or [])
                recommendations=self._draft_ranked_map_picks(draft_state.get("map"),excluded=excluded,limit=3)
                if recommendations:
                    body+="\nPick consigliati: "+", ".join(recommendations)
            await message.reply_text(body)
            return True

        # During the ban phase accept six plain Brawler names in one message.
        # Parse against the canonical Brawler catalog so compound names (Mr. P)
        # and small unique typos (grey -> Gray, pocho -> Poco) are accepted.
        if draft_state and draft_state.get("draft_format") in ("ban_all_pick","turn_pick") and len(draft_state.get("bans") or []) < 6:
            try:
                from difflib import SequenceMatcher
                catalog_rows=self._get("brawlers_catalog",{"select":"name_en,name_it"}) or []
            except Exception as exc:
                LOG.warning("DRAFT ban catalog unavailable: %s", type(exc).__name__)
                catalog_rows=[]
            aliases={}
            canonical=[]
            for row in catalog_rows:
                en=str(row.get("name_en") or "").strip()
                it=str(row.get("name_it") or en).strip()
                if not en:
                    continue
                display=it or en
                canonical.append(display)
                for alias in (en,it):
                    key=re.sub(r"[^a-z0-9]+"," ",str(alias).casefold()).strip()
                    if key:
                        aliases[key]=display
            # Common natural spellings that remain unambiguous.
            aliases.update({"mr p":"Mr. P","mr. p":"Mr. P","grey":"Gray","pocho":"Poco","maise":"Maisie"})
            words=[x for x in re.split(r"[\s,;]+",q.strip()) if x]
            parsed=[]
            i=0
            while i < len(words):
                matched=None
                # Prefer compound catalog names before single-word names.
                for width in (3,2,1):
                    if i+width > len(words):
                        continue
                    raw=" ".join(words[i:i+width])
                    key=re.sub(r"[^a-z0-9]+"," ",raw.casefold()).strip()
                    if key in aliases:
                        matched=(aliases[key],width)
                        break
                if not matched:
                    raw=words[i]
                    key=re.sub(r"[^a-z0-9]+"," ",raw.casefold()).strip()
                    scored=[]
                    for alias,display in aliases.items():
                        ratio=SequenceMatcher(None,key,alias).ratio()
                        if ratio >= 0.80:
                            scored.append((ratio,display))
                    scored.sort(reverse=True)
                    if scored and (len(scored)==1 or scored[0][0] > scored[1][0]+0.08):
                        matched=(scored[0][1],1)
                if not matched:
                    parsed=[]
                    break
                parsed.append(matched[0])
                i+=matched[1]
            if len(parsed) == 6:
                keys=[x.casefold() for x in parsed]
                if len(set(keys)) != 6:
                    await message.reply_text("Hai inserito un Brawler più di una volta. Inserisci 6 Brawler diversi.")
                    return True
                draft_state["bans"]=parsed
                context.user_data["ranked_draft"]=draft_state
                body="Ban registrati (6/6): "+", ".join(parsed)+"."
                if draft_state.get("draft_format") == "turn_pick":
                    body+="\nBan completati. Indica chi ha il primo pick: pick nostro oppure pick avversario."
                else:
                    body+="\nBan completati. Puoi procedere con le selezioni."
                await message.reply_text(body)
                return True

        auto_ban_q = re.fullmatch(r"(?:ban|banna|bannato)\\s+(.+)", q, re.I)
        if draft_state and auto_ban_q:
            draft_format=draft_state.get("draft_format")
            if draft_format == "all_pick":
                await message.reply_text("In questa fascia Ranked non è prevista la fase ban.")
                return True
            bans=draft_state.setdefault("bans",[])
            if len(bans) >= 6:
                await message.reply_text("I 6 ban della Draft sono già completi.")
                return True
            raw_brawler=auto_ban_q.group(1).strip()
            # A single explicit ban must pass through the same canonical catalog
            # used by the six-name ban flow. Never store arbitrary text as a ban.
            try:
                catalog_rows=self._get("brawlers_catalog",{"select":"name_en,name_it"}) or []
            except Exception as exc:
                LOG.warning("DRAFT single-ban catalog unavailable: %s", type(exc).__name__)
                catalog_rows=[]
            wanted=re.sub(r"[^a-z0-9]+"," ",raw_brawler.casefold()).strip()
            brawler=None
            for row in catalog_rows:
                en=str(row.get("name_en") or "").strip()
                it=str(row.get("name_it") or en).strip()
                keys={re.sub(r"[^a-z0-9]+"," ",x.casefold()).strip() for x in (en,it) if x}
                if wanted in keys:
                    brawler=it or en
                    break
            explicit_aliases={"mr p":"Mr. P","grey":"Gray","pocho":"Poco","maise":"Maisie"}
            if not brawler:
                brawler=explicit_aliases.get(wanted)
            if not brawler and wanted:
                import difflib
                fuzzy=[]
                for row in catalog_rows:
                    en=str(row.get("name_en") or "").strip(); it=str(row.get("name_it") or en).strip()
                    for alias in {en,it}:
                        key=re.sub(r"[^a-z0-9]+"," ",alias.casefold()).strip()
                        if key and difflib.SequenceMatcher(None,wanted,key).ratio() >= 0.80 and abs(len(key)-len(wanted)) <= 1:
                            fuzzy.append(it or en)
                unique=set(fuzzy)
                if len(unique)==1:
                    brawler=next(iter(unique))
            if not brawler:
                await message.reply_text(f"Non riconosco '{raw_brawler}' come Brawler. Riprova con il nome corretto.")
                return True
            normalized=lambda x: re.sub(r"[^a-z0-9]+"," ",str(x).casefold()).strip()
            if any(normalized(x) == normalized(brawler) for x in bans):
                await message.reply_text(f"{brawler} è già presente nei ban.")
                return True
            bans.append(brawler)
            context.user_data["ranked_draft"]=draft_state
            body=f"Ban registrato: {brawler}. Ban {len(bans)}/6."
            if len(bans) == 6:
                if draft_format == "turn_pick":
                    body+="\\nBan completati. "+self._draft_next_turn_text(draft_state)
                else:
                    body+="\\nBan completati. Puoi procedere con le selezioni."
            await message.reply_text(body)
            return True

        # Once the turn order is known, a plain Brawler name means the current turn's pick.
        # This matches the guided flow: the bot says whose turn it is, so users can answer simply "Edgar".
        plain_pick_q = None
        if draft_state and draft_state.get("draft_format") == "turn_pick" and draft_state.get("first_pick") in ("my","enemy") and len(draft_state.get("bans") or []) >= 6:
            try:
                catalog_rows=self._get("brawlers_catalog",{"select":"name_en,name_it"}) or []
            except Exception as exc:
                LOG.warning("DRAFT pick catalog unavailable: %s", type(exc).__name__)
                catalog_rows=[]
            wanted_pick=re.sub(r"[^a-z0-9]+"," ",q.casefold()).strip()
            for row in catalog_rows:
                en=str(row.get("name_en") or "").strip()
                it=str(row.get("name_it") or en).strip()
                aliases={re.sub(r"[^a-z0-9]+"," ",x.casefold()).strip() for x in (en,it) if x}
                if wanted_pick in aliases:
                    plain_pick_q=it or en
                    break
            if wanted_pick=="mr p": plain_pick_q="Mr. P"
            elif wanted_pick=="grey": plain_pick_q="Gray"
            elif wanted_pick=="pocho": plain_pick_q="Poco"
            # Conservative typo recovery: accept only a unique catalog name at
            # edit distance 1, so obvious slips such as Jackie/Edgat are corrected
            # without guessing between ambiguous Brawlers.
            if not plain_pick_q and wanted_pick:
                import difflib
                fuzzy=[]
                for row in catalog_rows:
                    en=str(row.get("name_en") or "").strip(); it=str(row.get("name_it") or en).strip()
                    for alias in {en,it}:
                        key=re.sub(r"[^a-z0-9]+"," ",alias.casefold()).strip()
                        if key and difflib.SequenceMatcher(None,wanted_pick,key).ratio() >= 0.80:
                            fuzzy.append((key,it or en))
                exactish={label for key,label in fuzzy if abs(len(key)-len(wanted_pick))<=1}
                if len(exactish)==1: plain_pick_q=next(iter(exactish))

        auto_pick_q = re.fullmatch(r"(?:pick|scelto|prende)\s+(.+)", q, re.I)
        if plain_pick_q and not auto_pick_q:
            auto_pick_q = re.fullmatch(r"(.+)", plain_pick_q)
        if draft_state and auto_pick_q and draft_state.get("draft_format") == "turn_pick":
            if draft_state.get("first_pick") not in ("my","enemy"):
                await message.reply_text("Prima indica chi ha il primo pick: primo pick nostro oppure primo pick avversario.")
                return True
            seq=draft_state.setdefault("pick_sequence",[])
            if len(seq) >= 6:
                await message.reply_text("I 6 pick della Draft sono già completi.")
                return True
            brawler=auto_pick_q.group(1).strip()
            # Never allow a banned or already selected Brawler to enter the Draft.
            used=(draft_state.get("bans") or [])+(draft_state.get("my_picks") or [])+(draft_state.get("enemy_picks") or [])
            used_keys={re.sub(r"[^a-z0-9]+"," ",str(x).casefold()).strip() for x in used if x}
            brawler_key=re.sub(r"[^a-z0-9]+"," ",brawler.casefold()).strip()
            if brawler_key in used_keys:
                await message.reply_text(f"{brawler} è già bannato o selezionato. Indica un altro Brawler.")
                return True
            side=self._draft_pick_order(draft_state["first_pick"])[len(seq)]
            seq.append({"side":side,"brawler":brawler})
            target="my_picks" if side == "my" else "enemy_picks"
            draft_state.setdefault(target,[]).append(brawler)
            context.user_data["ranked_draft"]=draft_state
            body=("Pick registrato per la NOSTRA SQUADRA: " if side == "my" else "Pick registrato per l'AVVERSARIO: ")+brawler+"."
            nxt=self._draft_next_turn_text(draft_state)
            if nxt: body+="\n"+nxt
            # On an enemy turn we only record the enemy pick and wait for the next input.
            # Recommendations are emitted exclusively when the next legal turn belongs to us.
            # Recommendations are driven by the map-specific Ranked meta below.
            # Do not expose the internal "counter unavailable" diagnostic in the guided Draft.
            excluded=(draft_state.get("bans") or [])+(draft_state.get("my_picks") or [])+(draft_state.get("enemy_picks") or [])
            ranked_picks=self._draft_ranked_map_picks(draft_state.get("map"),excluded=excluded,limit=8)
            if ranked_picks and len(seq) < 6:
                next_side=self._draft_pick_order(draft_state["first_pick"])[len(seq)]
                if next_side == "my":
                    recommendations=list(ranked_picks)
                    # Score against ALL enemy picks already locked, not only the
                    # latest one. Counter evidence is map+mode scoped and can only
                    # promote candidates that already pass this map's Ranked filter.
                    try:
                        catalog=self._get("brawlers_catalog",{"select":"brawler_id,name_en,name_it"}) or []
                        aliases={}
                        by_id={}
                        for x in catalog:
                            if x.get("brawler_id") is None: continue
                            bid=int(x["brawler_id"])
                            label=str(x.get("name_it") or x.get("name_en") or "")
                            by_id[bid]=label
                            for key in (x.get("name_en"),x.get("name_it")):
                                if key: aliases[str(key).strip().casefold()]=bid
                        evidence={x.casefold():{"map":0.0,"counter":0.0,"synergy":0.0,"samples":0} for x in ranked_picks}
                        base_index={x.casefold():i for i,x in enumerate(ranked_picks)}
                        candidate_ids={aliases.get(x.casefold()):x.casefold() for x in ranked_picks if aliases.get(x.casefold()) is not None}
                        # Exact-map individual performance.
                        map_rows=self._get("ranked_draft_stats",{
                            "select":"brawler_id,win_rate,games","stat_scope":"eq.map_pick",
                            "mode":"eq."+str(draft_state.get("mode_api") or ""),"map_name":"eq."+str(draft_state.get("map") or ""),"limit":"500"
                        }) or []
                        for row in map_rows:
                            bid=row.get("brawler_id")
                            if bid is None or int(bid) not in candidate_ids:continue
                            key=candidate_ids[int(bid)]
                            try: wr=float(row.get("win_rate") or 0); games=int(row.get("games") or 0)
                            except (TypeError,ValueError):continue
                            confidence=min(1.0,games/500.0)
                            evidence[key]["map"]=wr*confidence;evidence[key]["samples"]+=games
                        # All enemy picks: exact-map head-to-head evidence.
                        for enemy in (draft_state.get("enemy_picks") or []):
                            enemy_id=aliases.get(str(enemy).strip().casefold())
                            if enemy_id is None:continue
                            rows=self._get("ranked_draft_stats",{
                                "select":"brawler_id,win_rate,games","stat_scope":"eq.counter",
                                "other_brawler_id":f"eq.{enemy_id}","mode":"eq."+str(draft_state.get("mode_api") or ""),
                                "map_name":"eq."+str(draft_state.get("map") or ""),"limit":"500"
                            }) or []
                            for row in rows:
                                bid=row.get("brawler_id")
                                if bid is None or int(bid) not in candidate_ids:continue
                                key=candidate_ids[int(bid)]
                                try: wr=float(row.get("win_rate") or 0);games=int(row.get("games") or 0)
                                except (TypeError,ValueError):continue
                                confidence=min(1.0,games/250.0)
                                evidence[key]["counter"]+=wr*confidence;evidence[key]["samples"]+=games
                        # All our locked picks: exact-map teammate synergy.
                        for mate in (draft_state.get("my_picks") or []):
                            mate_id=aliases.get(str(mate).strip().casefold())
                            if mate_id is None:continue
                            rows=self._get("ranked_draft_stats",{
                                "select":"brawler_id,win_rate,games","stat_scope":"eq.synergy",
                                "other_brawler_id":f"eq.{mate_id}","mode":"eq."+str(draft_state.get("mode_api") or ""),
                                "map_name":"eq."+str(draft_state.get("map") or ""),"limit":"500"
                            }) or []
                            for row in rows:
                                bid=row.get("brawler_id")
                                if bid is None or int(bid) not in candidate_ids:continue
                                key=candidate_ids[int(bid)]
                                try: wr=float(row.get("win_rate") or 0);games=int(row.get("games") or 0)
                                except (TypeError,ValueError):continue
                                confidence=min(1.0,games/250.0)
                                evidence[key]["synergy"]+=wr*confidence;evidence[key]["samples"]+=games
                        # BrawlTrack common final comps are primary verified team-synergy evidence.
                        # They can promote only candidates already admitted by the exact-map Ranked filter.
                        try:
                            from live_maps import brawltrack_pro_map_stats
                            pro=brawltrack_pro_map_stats(draft_state.get("map")) or {}
                            chosen_ids={aliases.get(str(x).strip().casefold()) for x in (draft_state.get("my_picks") or [])}
                            chosen_ids.discard(None)
                            for comp in (pro.get("final_comps") or []):
                                if not isinstance(comp,dict): continue
                                team_ids={aliases.get(str(x).strip().casefold()) for x in (comp.get("team") or [])}
                                team_ids.discard(None)
                                if chosen_ids and not chosen_ids.issubset(team_ids): continue
                                sets=int(comp.get("sets") or 0); wr=float(comp.get("win_rate") or 0)
                                confidence=min(1.0,sets/50.0)
                                for bid,key in candidate_ids.items():
                                    if bid in team_ids and bid not in chosen_ids:
                                        evidence[key]["synergy"]+=wr*confidence
                                        evidence[key]["samples"]+=sets
                        except Exception as exc:
                            LOG.warning("DRAFT BrawlTrack comp scoring unavailable: %s",type(exc).__name__)
                        # Verified evidence dominates; existing exact-map Ranked order remains
                        # the safe fallback while matchup/synergy evidence fills.
                        # Weight the evidence by Draft context. Early picks need a strong,
                        # generally safe exact-map profile; later picks should react more
                        # aggressively to locked enemy picks and our existing composition.
                        my_locked=len(draft_state.get("my_picks") or [])
                        enemy_locked=len(draft_state.get("enemy_picks") or [])
                        if enemy_locked == 0 and my_locked == 0:
                            map_w,counter_w,synergy_w=1.0,0.0,0.0
                        elif enemy_locked == 0:
                            map_w,counter_w,synergy_w=0.75,0.0,1.15
                        elif my_locked >= 2:
                            map_w,counter_w,synergy_w=0.55,1.35,1.25
                        else:
                            map_w,counter_w,synergy_w=0.65,1.25,1.0
                        recommendations=sorted(ranked_picks,key=lambda x:(
                            evidence[x.casefold()]["map"]*map_w+
                            evidence[x.casefold()]["counter"]*counter_w+
                            evidence[x.casefold()]["synergy"]*synergy_w,
                            evidence[x.casefold()]["samples"],-base_index[x.casefold()]
                        ),reverse=True)
                    except Exception as exc:
                        LOG.warning("DRAFT multidimensional ranking unavailable: %s",type(exc).__name__)
                    body+="\nPick consigliati: "+", ".join(recommendations[:3])
            # Team-comp advice is actionable only when our side is about to pick (or once the draft is complete).
            next_side=None
            if len(seq) < 6:
                next_side=self._draft_pick_order(draft_state["first_pick"])[len(seq)]
            if next_side in ("my",None):
                comp=self.draft_comp_advice_text(draft_state)
                if comp: body+="\n"+comp
            await message.reply_text(body)
            return True

        # Stateful Draft accepts natural follow-ups without forcing one exact phrase.
        # Keep parsing conservative: only explicit pick/ban wording mutates the state.
        draft_actions = re.findall(
            r"(?:(mio|io)\\s+(?:pick|prendo)|(?:mio\\s+pick|pick\\s+mio))\\s+([^,;]+?)(?=\\s+(?:pick\\s+)?avversari[oa]\\b|\\s+avversari[oa]\\s+(?:pick|prende)\\b|$)"
            r"|(?:(?:pick\\s+)?avversari[oa]\\s+(?:pick|prende)?|avversari[oa]\\s+(?:pick|prende))\\s+([^,;]+?)(?=\\s+(?:mio|io)\\s+(?:pick|prendo)\\b|$)"
            r"|(?:ban|banna|bannato)\\s+([^,;]+)",
            q, re.I,
        )
        if draft_actions and draft_state:
            # Mythic+ is strictly turn-driven: never let the legacy free-form parser
            # bypass the 1-2-2-1 state machine or inject picks out of turn.
            if draft_state.get("draft_format") == "turn_pick":
                await message.reply_text(
                    "Draft a turni attiva. Inserisci un solo Brawler alla volta: "
                    + self._draft_next_turn_text(draft_state)
                )
                return True
            if any(action[3] for action in draft_actions):
                draft_format=draft_state.get("draft_format")
                if draft_format == "all_pick":
                    await message.reply_text("In questa fascia Ranked non è prevista la fase ban.")
                    return True
                current_bans=draft_state.setdefault("bans",[])
                incoming=[action[3].strip() for action in draft_actions if action[3]]
                room=max(0,6-len(current_bans))
                if not room:
                    await message.reply_text("I 6 ban della Draft sono già completi.")
                    return True
                if len(incoming) > room:
                    await message.reply_text(f"Puoi registrare ancora solo {room} ban: il totale massimo è 6.")
                    return True
            mine=[]; enemy=[]; bans=[]
            for action in draft_actions:
                if action[1]: mine.append(action[1].strip())
                if action[2]: enemy.append(action[2].strip())
                if action[3]: bans.append(action[3].strip())
            draft_state.setdefault("my_picks",[]).extend(x for x in mine if x)
            draft_state.setdefault("enemy_picks",[]).extend(x for x in enemy if x)
            draft_state.setdefault("bans",[]).extend(x for x in bans if x)
            context.user_data["ranked_draft"]=draft_state
            counter_response=None
            if enemy:
                counter_response=self.brawler_counter_text(enemy[-1], map_name=draft_state.get("map"))
                if not counter_response: counter_response=self.brawler_counter_text(enemy[-1])
            summary=[]
            if mine: summary.append("Miei pick: "+", ".join(draft_state["my_picks"]))
            if enemy: summary.append("Pick avversari: "+", ".join(draft_state["enemy_picks"]))
            if bans: summary.append("Ban: "+", ".join(draft_state["bans"]))
            comp_advice=self.draft_comp_advice_text(draft_state)
            body="Draft aggiornato. "+(" | ".join(summary) if summary else "Inserisci il prossimo pick.")
            if comp_advice: body+="\n"+comp_advice
            if counter_response: body+="\n"+counter_response
            await message.reply_text(body)
            return True

        counter_match = re.fullmatch(r"(?:counter(?:\s+di)?|chi\s+countera)\s+(.+)", q, re.I)
        if counter_match:
            response = self.brawler_counter_text(counter_match.group(1))
            if response:
                await message.reply_text(response)
                return True

        if ql in ("sito", "website", "web", "titaniabusivi.it"):
            await message.reply_text(
                "Sito ufficiale della community:\n"
                "https://www.titaniabusivi.it\n\n"
                "Qui trovi tutte le informazioni sulla community, i link ai nostri social ufficiali "
                "e l'accesso a Discord, utilizzato per le vocali della community."
            )
            return True

        if ql in ("discord", "server discord", "vocale", "vocali"):
            await message.reply_text(
                "Server Discord ufficiale della community:\n"
                "https://discord.gg/uwrUfsEaBc\n\n"
                "Usiamo Discord per le vocali della community."
            )
            return True

        owner_id = 437136453
        admin_register = re.fullmatch(r"registra\s+(?:utente\s+)?(?:@([A-Za-z0-9_]{3,32})|id\s+(\d+))\s+#?([A-Z0-9]{3,15})", q, re.I)
        if admin_register:
            if int(message.from_user.id) != owner_id:
                await message.reply_text("Comando riservato al proprietario del bot.")
                return True
            username, raw_id, raw_tag = admin_register.groups()
            raw_tag = raw_tag.upper()
            if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", raw_tag):
                await message.reply_text("Tag Brawl Stars non valido.")
                return True
            if username:
                target_rows=self._get("community_members",{"select":"*","telegram_username":f"ilike.{username}","limit":"1"}) or []
            else:
                target_rows=self._get("community_members",{"select":"*","telegram_user_id":f"eq.{int(raw_id)}","limit":"1"}) or []
            if not target_rows:
                await message.reply_text("Utente non trovato tra quelli censiti dal bot.")
                return True
            target=target_rows[0]
            current_tag=str(target.get("player_tag") or "").upper().replace("#","").strip()
            if current_tag:
                await message.reply_text(f"Giocatore già presente nel database: {target.get('player_name') or 'Account'} #{current_tag}.")
                return True
            tag_owner=self._get("community_members",{"select":"telegram_user_id","player_tag":f"eq.{raw_tag}","limit":"1"}) or []
            extra_owner=self._get("community_member_accounts",{"select":"telegram_user_id","player_tag":f"eq.{raw_tag}","is_active":"eq.true","limit":"1"}) or []
            if tag_owner or extra_owner:
                await message.reply_text("Questo tag Brawl Stars è già associato a un altro utente.")
                return True
            player=self.player_fetcher(raw_tag)
            if not player:
                await message.reply_text("Non riesco a verificare questo giocatore in questo momento. Nessuna registrazione effettuata.")
                return True
            payload={
                "player_tag":str(player["tag"]).replace("#","").upper(),
                "player_name":player["name"],
                "ranked_current":player.get("ranked_current"),
                "ranked_peak":player.get("ranked_peak"),
                "ranked_current_elo":player.get("ranked_current_elo"),
                "ranked_season_peak":player.get("ranked_season_peak"),
                "ranked_season_peak_elo":player.get("ranked_season_peak_elo"),
                "ranked_career_peak":player.get("ranked_career_peak"),
                "ranked_career_peak_elo":player.get("ranked_career_peak_elo"),
                "player_last_updated_at":self._now_iso(),
                "is_active":True,
            }
            self._patch("community_members",payload,params={"telegram_user_id":f"eq.{int(target.get('telegram_user_id'))}"})
            if self.snapshot_saver and player.get("trophies") is not None:
                try:self.snapshot_saver(player["tag"],player["name"],player["trophies"])
                except Exception as exc:print("ERRORE SNAPSHOT REGISTRAZIONE ADMIN:",repr(exc),flush=True)
            label=("@"+str(target.get("telegram_username"))) if target.get("telegram_username") else ("ID "+str(target.get("telegram_user_id")))
            await message.reply_text(f"Registrazione amministrativa completata: {label} → {player['name']} {player['tag']}.")
            return True

        reset_primary = re.fullmatch(r"ripristina\\s+registrazione\\s+primario\\s+@([A-Za-z0-9_]{3,32})", q, re.I)
        reset_secondary = re.fullmatch(r"ripristina\\s+registrazione\\s+secondario\\s+@([A-Za-z0-9_]{3,32})(?:\\s+(\\d+))?", q, re.I)
        if reset_primary or reset_secondary:
            if int(message.from_user.id) != owner_id:
                await message.reply_text("Comando riservato al proprietario del bot.")
                return True
            username=(reset_primary or reset_secondary).group(1)
            target_rows=self._get("community_members",{"select":"*","telegram_username":f"ilike.{username}","limit":"1"}) or []
            if not target_rows:
                await message.reply_text(f"Utente @{username} non trovato tra i registrati.")
                return True
            target=target_rows[0]
            target_id=int(target.get("telegram_user_id"))
            if reset_primary:
                old=self.admin_reset_primary_registration(target_id)
                await message.reply_text(f"Registrazione primaria ripristinata per @{username}. Il collegamento Telegram e i dati community sono stati mantenuti; ora può registrare un nuovo account principale.")
                return True
            number=reset_secondary.group(2)
            if not number:
                await message.reply_text(self.admin_secondary_accounts_text(target_id))
                return True
            old=self.admin_reset_secondary_registration(target_id,int(number))
            if not old:
                await message.reply_text(f"Account secondario {number} non trovato per @{username}.")
            else:
                await message.reply_text(f"Account secondario {number} ripristinato per @{username}: {old.get('player_name') or 'Account'} #{old.get('player_tag')}. L'account principale non è stato modificato.")
            return True

        extra_match = re.fullmatch(r"(?:aggiungi\\s+account|aggiungi\\s+profilo)\\s*#?([A-Z0-9]{3,15})", q, re.I)
        if extra_match:
            raw_tag=extra_match.group(1).upper()
            if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}",raw_tag):
                await message.reply_text("Tag Brawl Stars non valido. Controllalo e riprova con: aggiungi account #TAG.")
                return True
            try:
                player=self.add_additional_account(message,raw_tag)
                if not player:
                    await message.reply_text("Non riesco a trovare quel giocatore. Controlla il tag.")
                elif player.get("_needs_primary"):
                    await message.reply_text("Prima registra il tuo account principale con: registrami #TAG")
                elif player.get("_duplicate"):
                    await message.reply_text("Questo è già il tuo account principale.")
                elif player.get("_tag_in_use"):
                    await message.reply_text("Questo tag Brawl Stars è già registrato e non può essere collegato a un altro utente.")
                else:
                    n=max(1,int(player.get("_account_order") or 2)-1)
                    await message.reply_text(f"Account secondario {n} collegato: {player['name']} {player['tag']} - {self.number_formatter(player.get('trophies') or 0)} trofei.")
            except Exception as exc:
                print("ERRORE AGGIUNTA ACCOUNT:",repr(exc),flush=True)
                await message.reply_text("Non riesco ad aggiungere questo account in questo momento.")
            return True

        if ql in ("i miei account","miei account","account collegati"):
            await message.reply_text(self.my_accounts_text(message.from_user.id))
            return True

        match = re.fullmatch(r"(?:registrami|tegistrami)\s*#?([A-Z0-9]{3,15})", q, re.I)
        if match:
            raw_tag = match.group(1).upper()
            if not re.fullmatch(r"[0289PYLQGRJCUV]{3,15}", raw_tag):
                await message.reply_text(
                    "Tag Brawl Stars non valido. Controllalo e riprova con: registrami #TAG. "
                    "Attenzione: nei tag Brawl Stars la lettera O non è valida; potrebbe essere uno zero (0)."
                )
                return True
            try:
                player = self.register_member(message, raw_tag)
                if not player:
                    await message.reply_text("Non riesco a trovare quel giocatore. Controlla il tag.")
                elif player.get("_registration_locked"):
                    await message.reply_text("Giocatore già presente nel database.")
                else:
                    ranked_current = player.get("ranked_current")
                    ranked_peak = player.get("ranked_peak")
                    ranked_season_peak = player.get("ranked_season_peak")
                    club_name = player.get("club_name") or "Senza club / non disponibile"

                    def fmt(value):
                        return self.number_formatter(value) if value is not None else "Non disponibile"

                    owned_brawlers = int(player.get("brawlers") or 0)
                    total_brawlers = player.get("brawlers_total")
                    brawler_text = f"{fmt(owned_brawlers)}/{fmt(total_brawlers)}" if total_brawlers else fmt(owned_brawlers)
                    level_lines = []
                    for level, count in sorted((player.get("power_levels") or {}).items(), key=lambda item: int(item[0])):
                        if count:
                            level_lines.append(f"Livello {level}: {fmt(count)}/{fmt(owned_brawlers)}")
                    prestige_lines = []
                    for level, count in sorted((player.get("prestige_levels") or {}).items(), key=lambda item: int(item[0])):
                        if count:
                            prestige_lines.append(f"Prestigio {level}: {fmt(count)}/{fmt(owned_brawlers)}")
                    def owned_total(owned_key, total_key):
                        owned = player.get(owned_key)
                        total = player.get(total_key)
                        return f"{fmt(owned)}/{fmt(total)}" if total is not None else fmt(owned)
                    fame_tier = str(player.get("fame_tier") or "").strip()
                    fame_text = fame_tier or "Non disponibile"
                    fame_score_text = None
                    fame_levels = {
                        "global": ("Fama globale", 0, 2000),
                        "lunar": ("Fama lunare", 6000, 3200),
                        "martian": ("Fama marziana", 15600, 4500),
                        "saturnian": ("Fama saturniana", 29100, 8000),
                        "solar": ("Fama solare", 53100, 12000),
                        "meteoric": ("Fama meteorica", 89100, 20000),
                        "alien": ("Fama aliena", 149100, 50000),
                        "starr force": ("Fama Starr Force", 299100, 75000),
                    }
                    fame_value = player.get("fame")
                    for tier_name, (label, tier_start, per_level) in fame_levels.items():
                        if tier_name in fame_tier.casefold():
                            roman_match = re.search(r"\\b(I{1,3})\\b", fame_tier, re.I)
                            roman = roman_match.group(1).upper() if roman_match else "I"
                            level_index = {"I": 0, "II": 1, "III": 2}.get(roman, 0)
                            fame_text = f"{label} {roman}"
                            if fame_value is not None:
                                progress = max(0, int(fame_value) - tier_start - (level_index * per_level))
                                fame_text += f" — {fmt(progress)}/{fmt(per_level)}"
                                fame_score_text = f"Punteggio Fama: {fmt(int(fame_value))}"
                            break
                    lines = [
                        f"ACCOUNT COLLEGATO: {str(player.get('name') or '').upper()}",
                        f"Tag: {player.get('tag')}",
                        f"Club: {club_name}",
                        f"Tag club: {player.get('club_tag') or 'Non disponibile'}", "",
                        "PROFILO",
                        f"Trofei: {fmt(player.get('trophies'))}",
                        f"Brawler: {brawler_text}",
                        f"Livello: {fmt(player.get('level'))}",
                        f"Punti esperienza: {fmt(player.get('exp_points'))}",
                        f"Fama: {fame_text}",
                        *([fame_score_text] if fame_score_text else []),
                        f"Livello Clip: {fmt(player.get('clip_level'))}",
                        f"Punti Clip: {fmt(player.get('clip_points'))}",
                        *([f"Account creato nel: {fmt(player.get('account_created_year'))}"] if player.get("account_created_year") is not None else []),
                        f"Qualificazione Championship: {'Qualificato' if player.get('championship_qualified') else 'Mai qualificato'}", "",
                        "CLASSIFICATA",
                        f"Classificata attuale: {ranked_current or 'Non disponibile'}",
                        f"Record stagione: {ranked_season_peak or 'Non disponibile'}",
                        f"Record massimo: {ranked_peak or 'Non disponibile'}", "",
                        "VITTORIE",
                        f"3v3: {fmt(player.get('wins_3v3'))}",
                        f"Solo: {fmt(player.get('wins_solo'))}",
                        f"Duo: {fmt(player.get('wins_duo'))}", "",
                        "COLLEZIONE",
                        f"Skin: {owned_total('skins_owned','skins_total')}",
                        *([f"Valore skin: {fmt(int(player.get('skin_value_gems')))} gemme"] if player.get("skin_value_gems") is not None else []),
                        *([f"Valore equivalente: {float(player.get('skin_value_eur')):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")] if player.get("skin_value_eur") is not None else []),
                        f"Gadget: {owned_total('gadgets_owned','gadgets_total')}",
                        f"Abilità stellari: {owned_total('star_powers_owned','star_powers_total')}",
                        f"Equipaggiamenti: {owned_total('gears_owned','gears_total')}",
                        f"Overdrive: {owned_total('hypercharges_owned','hypercharges_total')}",
                        f"Buffie: {owned_total('buffies_owned','buffies_total')}", "",
                        "LIVELLI BRAWLER", *level_lines, "",
                        "PRESTIGIO BRAWLER",
                        f"Prestigi totali: {fmt(player.get('prestige'))}", *prestige_lines, "",
                        "TEMPO DI GIOCO",
                        f"Ore giocate stimate: {fmt(player.get('estimated_hours'))} h" if player.get("estimated_hours") is not None else "Ore giocate stimate: Non disponibile", "",
                        "COSTO PER MAXARE L'ACCOUNT",
                        *(["ACCOUNT MAXATO"] if all(player.get(key) == 0 for key in ("max_cost_coins", "max_cost_power_points", "gears_missing_cost")) else [
                            f"Monete mancanti: {fmt(player.get('max_cost_coins'))}",
                            f"Punti energia mancanti: {fmt(player.get('max_cost_power_points'))}",
                            f"Costo Equipaggiamenti mancanti: {fmt(player.get('gears_missing_cost'))} monete",
                        ]),
                        "",
                    ]
                    await message.reply_text("\n".join(lines))
            except Exception as exc:
                print("ERRORE REGISTRAZIONE:", repr(exc), flush=True)
                await message.reply_text("Non riesco a salvare la registrazione. Verifica che lo schema community sia stato creato su Supabase.")
            return True

        stat_aliases = {
            "3v3":"3v3", "vittorie 3v3":"3v3", "solo":"solo", "vittorie solo":"solo",
            "duo":"duo", "vittorie duo":"duo", "trofei":"trofei", "coppe":"trofei",
            "brawler":"brawler", "brawlers":"brawler", "livello":"livello", "livello account":"livello",
            "prestigio":"prestigio",
            "classificata":"classificata", "ranked":"classificata",
            "classificata attuale":"classificata", "ranked attuale":"classificata",
            "classificata stagione":"classificata stagione", "ranked stagione":"classificata stagione",
            "record classificata stagione":"classificata stagione", "record ranked stagione":"classificata stagione",
            "classificata carriera":"classificata carriera", "ranked carriera":"classificata carriera",
            "record classificata carriera":"classificata carriera", "record ranked carriera":"classificata carriera",
        }
        if ql in ("statistiche","stats community","statistiche community","tutte le statistiche","tutte le classifiche"):
            await _ranking_reply(self.all_stats_text(_ranking_chat_id)); return True
        club_all=re.fullmatch(r"(?:statistiche|stats|tutte le statistiche|tutte le classifiche)(?:\s+(?:del|dei|di))?\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)",q,re.I)
        if club_all:
            await _ranking_reply(self.all_stats_text(_ranking_chat_id,self.CLUB_ALIASES[club_all.group(1).lower()])); return True
        stat=re.fullmatch(r"classific(?:a|he)(?:\s+(?:player|giocatori))?(?:\s+(?:della\s+)?community)?(?:\s+(?:per|di))?\s+(3v3|vittorie 3v3|solo|vittorie solo|duo|vittorie duo|trofei|coppe|brawlers?|livello(?: account)?|prestigio|classificata(?: attuale| stagione| carriera)?|ranked(?: attuale| stagione| carriera)?|record classificata (?:stagione|carriera)|record ranked (?:stagione|carriera))",q,re.I)
        if stat:
            await _ranking_reply(self.stat_ranking_text(_ranking_chat_id, stat_aliases[stat.group(1).lower()])); return True
        club_default=re.fullmatch(r"classific(?:a|he)\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)",q,re.I)
        if club_default:
            club_name=self.CLUB_ALIASES[club_default.group(1).lower()]
            await message.reply_text(self.stat_ranking_text(_ranking_chat_id, "trofei", club_name))
            return True
        clubstat=re.fullmatch(r"classific(?:a|he)\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?)(?:\s+(?:per|di))?\s+(3v3|vittorie 3v3|solo|vittorie solo|duo|vittorie duo|trofei|coppe|brawlers?|livello(?: account)?|prestigio|classificata(?: attuale| stagione| carriera)?|ranked(?: attuale| stagione| carriera)?|record classificata (?:stagione|carriera)|record ranked (?:stagione|carriera))",q,re.I)
        if clubstat:
            await _ranking_reply(self.stat_ranking_text(_ranking_chat_id, stat_aliases[clubstat.group(2).lower()], self.CLUB_ALIASES[clubstat.group(1).lower()])); return True
        if re.search(r"\bclassific(?:a|he)\b",ql) and "3v3" in ql:
            club_name=next((v for k,v in self.CLUB_ALIASES.items() if k in ql),None)
            await _ranking_reply(self.stat_ranking_text(_ranking_chat_id, "3v3", club_name)); return True
        ranked_delta = re.fullmatch(
            r"classific(?:a|he)(?:\s+(titani(?: abusivi)?|tamarri(?: abusivi)?|tornadi(?: abusivi)?|talenti(?: abusivi)?))?\s+(?:elo\s+)?(?:ranked|classificata)(?:\s+(oggi|7|15|30)(?:\s+giorni)?)?",
            q, re.I,
        )
        if ranked_delta:
            club_key = ranked_delta.group(1)
            club_name = self.CLUB_ALIASES.get(club_key.lower()) if club_key else None
            period = ranked_delta.group(2) or "oggi"
            days = 0 if period.lower() == "oggi" else int(period)
            await _ranking_reply(self.ranked_elo_ranking_text(_ranking_chat_id, days, club_name))
            return True

        if re.search(r"\bclassific(?:a|he)\b", ql) and re.search(r"\b(?:classificata|ranked)\b", ql):
            club_name=next((v for k,v in self.CLUB_ALIASES.items() if k in ql),None)
            stat_key="classificata carriera" if "carriera" in ql else ("classificata stagione" if "stagione" in ql else "classificata")
            await _ranking_reply(self.stat_ranking_text(_ranking_chat_id, stat_key, club_name)); return True

        match = re.fullmatch(
            r"classifica(?:\s+(?:della\s+community))?(?:\s+di)?\s+oggi",
            q,
            re.I,
        )
        if match:
            await _ranking_reply(self.ranking_text(_ranking_chat_id, 0))
            return True

        match = re.fullmatch(
            r"classifica(?:\s+(?:della\s+community))?\s+(?:(?:degli\s+)?ultimi\s+)?(7|15|30)(?:\s+giorni)?",
            q,
            re.I,
        )
        if match:
            days = int(match.group(1))
            await _ranking_reply(self.ranking_text(_ranking_chat_id, days))
            return True

        if ql in ("club", "profilo club", "stato club"):
            await message.reply_text(self.club_summary_text(message.chat_id))
            return True

        if ql in ("elenco utenti", "elenco registrati", "registrati", "membri registrati", "account registrati"):
            await message.reply_text(self.registered_members_text(message.chat_id))
            return True

        if ql in ("elenco inattivi", "elenco utenti inattivi", "inattivi", "inattivita", "inattività"):
            await message.reply_text(self.inactivity_text(message.chat_id))
            return True

        match = re.fullmatch(r"assenza\s+(\d{1,3})", q, re.I)
        if match:
            days = max(1, min(90, int(match.group(1))))
            until = self.set_vacation(message.chat_id, message.from_user.id, days)
            await message.reply_text(f"Assenza registrata fino al {until.astimezone(ROME).strftime('%d/%m/%Y')}. In quel periodo non sarai segnalato come inattivo.")
            return True

        if ql == "eventi":
            await message.reply_text(self.events_text(message.chat_id))
            return True

        match = re.fullmatch(r"partecipo\s+(\d+)", q, re.I)
        if match:
            ok = self.confirm_event(int(match.group(1)), message.from_user.id)
            await message.reply_text("Partecipazione confermata." if ok else "Evento non trovato o non più aperto.")
            return True

        match = re.fullmatch(r"evento\s+crea\s+(.+?)\s*\|\s*(.+?)(?:\s*\|\s*(obbligatorio))?", q, re.I)
        if match:
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
                return True
            try:
                event = self.create_event(
                    message.chat_id,
                    message.from_user.id,
                    match.group(1),
                    match.group(2),
                    bool(match.group(3)),
                )
                await message.reply_text(f"Evento creato. ID {event['id']}\n{self.events_text(message.chat_id)}")
            except ValueError:
                await message.reply_text("Formato: evento crea NOME | GG/MM/AAAA HH:MM | obbligatorio")
            except Exception as exc:
                print("ERRORE CREAZIONE EVENTO:", repr(exc), flush=True)
                await message.reply_text("Non riesco a creare l'evento. Verifica lo schema Supabase.")
            return True

        if ql == "reclutamento":
            context.user_data["recruitment_stage"] = "tag"
            await message.reply_text("Candidatura TITANI ABUSIVI. Inviami il tuo tag Brawl Stars.")
            return True

        if ql == "candidature":
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
            else:
                await message.reply_text(self.recruitments_text(message.chat_id))
            return True

        report_match = re.fullmatch(
            r"report(?:\s+(community|club|globale\s+club|titani|tamarri|tornadi|talenti|club\s+globale\s+(?:titani|tamarri|tornadi|talenti)))?\s*(oggi|giornaliero|7|15|30|mensile)?",
            q, re.I,
        )
        if report_match:
            raw_scope = (report_match.group(1) or "community").casefold()
            requested = (report_match.group(2) or "7").casefold()
            if requested in ("oggi", "giornaliero") and report_match.group(1) is None:
                report_text = await asyncio.to_thread(self.operational_report_text, message.chat_id, "daily")
                await message.reply_text(report_text)
                return True
            days = 0 if requested in ("oggi", "giornaliero") else (30 if requested == "mensile" else int(requested))
            if raw_scope == "community":
                scope = "community"
            elif raw_scope == "club":
                scope = "community_club"
            elif raw_scope == "globale club":
                scope = "global_clubs"
            elif raw_scope.startswith("club globale "):
                scope = "global_single:" + raw_scope.removeprefix("club globale ").strip()
            else:
                scope = raw_scope
            LOG.info("MANUAL REPORT BUILD START chat=%s scope=%s days=%s", message.chat_id, scope, days)
            try:
                report_text = await asyncio.wait_for(asyncio.to_thread(self.periodic_report_text, message.chat_id, scope, days), timeout=60)
            except asyncio.TimeoutError:
                LOG.error("MANUAL REPORT BUILD TIMEOUT chat=%s scope=%s days=%s", message.chat_id, scope, days)
                await message.reply_text("Il report sta impiegando troppo tempo. Riprova tra poco.")
                return True
            LOG.info("MANUAL REPORT BUILD DONE chat=%s scope=%s days=%s", message.chat_id, scope, days)
            await message.reply_text(report_text)
            LOG.info("MANUAL REPORT PRIVATE DELIVERY DONE user=%s", message.from_user.id)
            return True

        match = re.fullmatch(r"report\s+(giornaliero|settimanale)\s+(on|off)", q, re.I)
        if match:
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
                return True
            key = "daily_report_enabled" if match.group(1).lower() == "giornaliero" else "weekly_report_enabled"
            enabled = match.group(2).lower() == "on"
            self.set_settings(message.chat_id, **{key: enabled})
            await message.reply_text(f"Report {match.group(1).lower()} automatico {'attivato' if enabled else 'disattivato'}.")
            return True

        match = re.fullmatch(r"autokick\s+(on|off)", q, re.I)
        if match:
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
                return True
            enabled = match.group(1).lower() == "on"
            self.set_settings(message.chat_id, auto_kick=enabled)
            await message.reply_text(f"Auto-kick {'ATTIVO' if enabled else 'disattivato'}. L'auto-kick considera solo l'attività che il bot vede nel gruppo.")
            return True

        match = re.fullmatch(r"soglie\s+inattivit[àa]\s+(\d+)\s+(\d+)", q, re.I)
        if match:
            if not await self.is_admin(context, message.chat_id, message.from_user.id):
                await message.reply_text("Questo comando è riservato agli amministratori del gruppo.")
                return True
            warn = int(match.group(1))
            kick = int(match.group(2))
            if warn < 1 or kick <= warn:
                await message.reply_text("La soglia kick deve essere maggiore della soglia avviso.")
            else:
                self.set_settings(message.chat_id, inactivity_warn_days=warn, inactivity_kick_days=kick)
                await message.reply_text(f"Soglie aggiornate: avviso {warn} giorni, kick {kick} giorni.")
            return True

        return False

    def skin_history_chart(self, registered_user, category="Totale", days=30):
        """Build a PNG chart from real Skin Account snapshots."""
        if not registered_user or not registered_user.get("id"):
            return None, "Devi prima registrare il tuo tag Brawl Stars."
        try:
            rows = self._get("skin_account_history", {
                "select": "snapshot_date,owned_count,total_count,gained_count",
                "community_member_id": f"eq.{int(registered_user['id'])}",
                "category": f"eq.{category}",
                "order": "snapshot_date.asc",
                "limit": str(max(2, min(int(days or 30), 365))),
            })
            if not rows:
                return None, "Non ci sono ancora dati storici Skin Account per questo account."
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            dates = [datetime.strptime(r["snapshot_date"], "%Y-%m-%d") for r in rows]
            owned = [int(r.get("owned_count") or 0) for r in rows]
            totals = [int(r.get("total_count") or 0) for r in rows]
            fig, ax = plt.subplots(figsize=(10, 5.6))
            ax.plot(dates, owned, marker="o", linewidth=2, label="Possedute")
            ax.plot(dates, totals, marker="o", linewidth=1.5, linestyle="--", label="Totali disponibili")
            for i, (x, y) in enumerate(zip(dates, owned)):
                if i == 0:
                    label = str(y)
                else:
                    gain = max(0, y - owned[i - 1])
                    label = f"{y} (+{gain})"
                ax.annotate(label, (x, y), xytext=(0, 9), textcoords="offset points", ha="center", fontsize=9)
            ax.set_title(f"Skin Account — {category}")
            ax.set_xlabel("Data")
            ax.set_ylabel("Numero di skin")
            ax.grid(True, alpha=0.25)
            ax.legend()
            fig.autofmt_xdate()
            fig.tight_layout()
            output = io.BytesIO()
            fig.savefig(output, format="png", dpi=150)
            plt.close(fig)
            output.seek(0)
            return output, None
        except Exception as exc:
            print("ERRORE GRAFICO SKIN:", repr(exc), flush=True)
            return None, "Non riesco a generare il grafico Skin Account in questo momento."

    def save_daily_skin_snapshots(self, snapshot_date=None):
        """Save one monotonic daily Skin Account snapshot per active registered member."""
        day = snapshot_date or datetime.now(timezone.utc).astimezone(ROME).date().isoformat()
        members = self._get("community_members", {
            "select": "id,player_tag",
            "is_active": "eq.true",
            "player_tag": "not.is.null",
            "order": "id.asc",
        })
        catalog = []
        offset = 0
        while True:
            page = self._get("skins_catalog", {
                "select": "external_id,rarity,source_payload",
                "verification_status": "eq.structured_verified",
                "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
                "limit": "1000",
                "offset": str(offset),
            })
            catalog.extend(page)
            if len(page) < 1000:
                break
            offset += 1000
        totals = {"Totale": len(catalog)}
        for row in catalog:
            label = self._skin_category_label(row)
            totals[label] = totals.get(label, 0) + 1
        saved = 0
        skipped = 0
        for member in members or []:
            member_id = int(member["id"])
            existing = self._get("skin_account_history", {
                "select": "id",
                "community_member_id": f"eq.{member_id}",
                "snapshot_date": f"eq.{day}",
                "category": "eq.Totale",
                "limit": "1",
            })
            if existing:
                skipped += 1
                continue
            try:
                owned_ids = self._official_owned_skin_ids(member["player_tag"])
                current = {"Totale": 0}
                for row in catalog:
                    ext = row.get("external_id")
                    if ext is None or int(ext) not in owned_ids:
                        continue
                    current["Totale"] += 1
                    label = self._skin_category_label(row)
                    current[label] = current.get(label, 0) + 1
                previous_rows = self._get("skin_account_history", {
                    "select": "category,owned_count",
                    "community_member_id": f"eq.{member_id}",
                    "snapshot_date": f"lt.{day}",
                    "order": "snapshot_date.desc",
                    "limit": "50",
                })
                previous = {}
                for row in previous_rows or []:
                    previous.setdefault(str(row.get("category")), int(row.get("owned_count") or 0))
                payload = []
                for category, total in totals.items():
                    observed = int(current.get(category, 0))
                    old = previous.get(category)
                    # Owned skins are monotonic. A lower API observation is treated as incomplete.
                    owned = max(observed, old) if old is not None else observed
                    gained = max(0, owned - old) if old is not None else 0
                    payload.append({
                        "community_member_id": member_id,
                        "player_tag": member["player_tag"],
                        "snapshot_date": day,
                        "category": category,
                        "owned_count": owned,
                        "total_count": max(int(total), owned),
                        "gained_count": gained,
                    })
                self._post("skin_account_history", payload, params={"on_conflict": "community_member_id,snapshot_date,category"}, prefer="resolution=merge-duplicates,return=minimal")
                saved += 1
            except Exception as exc:
                if isinstance(exc, SkinBridgeBackoff):
                    print("SKIN DAILY SNAPSHOT: upstream circuit open; remaining members deferred", flush=True)
                    break
                if isinstance(exc, requests.HTTPError):
                    # _official_owned_skin_ids already emitted the bounded status/failure count.
                    # Avoid duplicating one red traceback-style line per attempted member.
                    continue
                print(f"ERRORE SNAPSHOT SKIN MEMBER {member_id}:", repr(exc), flush=True)
        print(f"SKIN DAILY SNAPSHOT: date={day} saved={saved} skipped={skipped}", flush=True)
        return {"date": day, "saved": saved, "skipped": skipped}

    async def scheduled_jobs(self, context):
        if not self.ready:
            return
        now_utc = datetime.now(timezone.utc)
        now_rome = now_utc.astimezone(ROME)

        try:
            self.save_daily_skin_snapshots(now_rome.date().isoformat())
        except Exception as exc:
            print("ERRORE JOB SNAPSHOT SKIN:", repr(exc), flush=True)

        try:
            events = self._get(
                "community_events",
                {
                    "select": "*",
                    "status": "eq.open",
                    "reminder_sent": "eq.false",
                    "event_at": f"gte.{now_utc.isoformat()}",
                    "order": "event_at.asc",
                },
            )
            for event in events:
                dt = self._parse_dt(event.get("event_at"))
                if not dt or dt - now_utc > timedelta(hours=24):
                    continue
                local = dt.astimezone(ROME).strftime("%d/%m/%Y %H:%M")
                await context.bot.send_message(
                    chat_id=int(event["chat_id"]),
                    text=(
                        f"PROMEMORIA EVENTO\n{event['name']} - {local}"
                        f"{' - OBBLIGATORIO' if event.get('mandatory') else ''}\n"
                        f"Conferma con: partecipo {event['id']}"
                    ),
                )
                self._patch("community_events", {"reminder_sent": True}, params={"id": f"eq.{event['id']}"})
        except Exception as exc:
            print("ERRORE JOB EVENTI:", repr(exc), flush=True)

        try:
            settings_rows = self._get("community_settings", {"select": "*"})
        except Exception as exc:
            print("ERRORE JOB SETTINGS:", repr(exc), flush=True)
            settings_rows = []

        for settings in settings_rows:
            chat_id = int(settings["chat_id"])
            report_hour = int(settings.get("report_hour") or 9)

            if now_rome.hour == report_hour:
                today_key = now_rome.strftime("%Y-%m-%d")
                if settings.get("daily_report_enabled") and settings.get("last_daily_report_date") != today_key:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=self.operational_report_text(chat_id, "daily"))
                        self.set_settings(chat_id, last_daily_report_date=today_key)
                    except Exception as exc:
                        print("ERRORE REPORT GIORNALIERO:", repr(exc), flush=True)

                week_key = f"{now_rome.isocalendar().year}-W{now_rome.isocalendar().week}"
                if now_rome.weekday() == 0 and settings.get("weekly_report_enabled") and settings.get("last_weekly_report_key") != week_key:
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=self.operational_report_text(chat_id, "weekly"))
                        self.set_settings(chat_id, last_weekly_report_key=week_key)
                    except Exception as exc:
                        print("ERRORE REPORT SETTIMANALE:", repr(exc), flush=True)

            try:
                inactive = self.inactivity_rows(chat_id)
                warn_days = int(settings.get("inactivity_warn_days") or 5)
                kick_days = int(settings.get("inactivity_kick_days") or 10)
                auto_kick = bool(settings.get("auto_kick"))
                for member, days, risk in inactive:
                    last_warning = self._parse_dt(member.get("last_warning_at"))
                    if days >= warn_days and (not last_warning or now_utc - last_warning >= timedelta(hours=24)):
                        name = member.get("display_name") or member.get("telegram_username") or str(member.get("telegram_user_id"))
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Avviso inattività: {name} risulta inattivo nel gruppo da {days} giorni. Soglia kick: {kick_days} giorni.",
                        )
                        self._patch(
                            "community_members",
                            {"last_warning_at": now_utc.isoformat()},
                            params={
                                "chat_id": f"eq.{chat_id}",
                                "telegram_user_id": f"eq.{int(member['telegram_user_id'])}",
                            },
                        )
            except Exception as exc:
                print("ERRORE JOB INATTIVITA:", repr(exc), flush=True)
