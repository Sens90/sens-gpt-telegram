"""Italian explanation of the current, explicitly versioned coefficient model."""

from trophy_coefficient import TROPHY_COEFFICIENT_BANDS, PREMIUM_CAP


def coefficient_guide_lines():
    lines = [
        "IL PRIMO E UNICO SISTEMA AL MONDO PER IL COEFFICIENTE ABUSIVO",
        "COS'È IL COEFFICIENTE ABUSIVO",
        "📊 È un indice strutturale della distribuzione attuale delle coppe, calcolato Brawler per Brawler.",
        "🏆 Ogni coppa reale contribuisce almeno ×1. Le coppe nelle fasce difficili ricevono un premio aggiuntivo.",
        "🧮 Formula: somma dei punteggi ponderati dei Brawler ÷ trofei totali ufficiali.",
        "📌 Il punteggio ponderato viene arrotondato all'intero; il coefficiente è mostrato con sei decimali.",
        "📌 Se il totale ufficiale supera la somma delle coppe nei Brawler disponibili, la differenza entra a peso ×1.",
        "FASCE E PESI ATTUALI",
    ]
    for start, end, weight in TROPHY_COEFFICIENT_BANDS:
        lines.append(f"🏆 {start:,}–{end-1:,} coppe: ×{weight:.4f}".replace(",", ".").replace("×1.", "×1,").replace("×2.", "×2,"))
    lines.extend([
        f"🏆 Da {PREMIUM_CAP:,} coppe sullo stesso Brawler, le coppe successive aggiungono ×1 ciascuna.".replace(",", "."),
        "COME SI CALCOLA UN BRAWLER",
        "🎯 Ogni fascia pesa soltanto le coppe che cadono al suo interno. Passare alla fascia seguente non ricalcola le coppe precedenti.",
        "📈 Il punteggio totale è la somma dei punteggi di tutti i Brawler; il coefficiente divide quel punteggio per le coppe ufficiali.",
        "PERCHÉ 2.000 COPPE CAMBIANO LA DIFFICOLTÀ",
        "⚡ Sotto 2.000 coppe il sistema di gioco prevede serie di vittorie fino a +10, partite con bot e sfavorito.",
        "🔒 Da 2.000 coppe questi meccanismi cessano: la difficoltà strutturale cambia e i pesi attuali aumentano.",
        "🎮 I pesi descrivono la difficoltà delle fasce. Il coefficiente strutturale non tenta di ricostruire le modalità storiche di ogni coppa.",
        "ECONOMIA DELLE BATTAGLIE OSSERVATE",
        "🎮 Il monitor salva il delta reale della battaglia, il Brawler, la fascia, la modalità, il risultato e la squadra quando disponibili.",
        "🧮 La base prevista dipende dalla fascia, dalla modalità e dal piazzamento o risultato, secondo le tabelle verificate.",
        "✨ Extra osservato = coppe reali della battaglia meno delta base previsto, quando la base è verificabile.",
        "🔥 Una serie di vittorie può aggiungere fino a +10 sotto 2.000. Un extra da solo non prova la sua causa: può comprendere sfavorito o altri effetti.",
        "🔍 Il report usa «Bonus osservato +X» quando non è possibile dimostrare il tipo preciso dal battle log.",
        "📌 Sopra 2.000 la classificazione della base resta non disponibile finché le regole pertinenti non sono verificate.",
        "PROGRESSIONE PARTITA PER PARTITA",
        "📈 Per una partita con delta positivo, i punti sono la differenza tra il punteggio ponderato prima e dopo il delta reale.",
        "👥 Nelle modalità di squadra, Team Value è il massimo delle coppe del Brawler nella squadra reale: il riferimento è il maggiore tra Team Value e coppe del proprio Brawler.",
        "🧍 In Sopravvivenza in singolo il riferimento sono le coppe del proprio Brawler: Squadra: Modalità Solo.",
        "❌ Una sconfitta vale 0 punti Progressione; il delta negativo reale aggiorna comunque le coppe usate per la partita successiva.",
        "🤝 Un pareggio con coppe positive mantiene il risultato Pareggio e conta il delta reale, senza diventare una vittoria o una serie di vittorie.",
        "🕐 La Progressione usa soltanto battaglie osservate e valide. Se il battle log non copre un intervallo, non si assegnano coppe a Brawler ipotetici.",
        "DATI ESCLUSI DAL COEFFICIENTE STRUTTURALE",
        "🚫 Vittorie 3v3/Solo/Duo, Miglior giocatore, Classificata, Prestigio e livello account non entrano nella formula strutturale.",
        "COMANDI",
        "📊 coefficiente abusivo #TAG — valore strutturale dell'account.",
        "📈 progressione oggi #TAG — dettaglio delle battaglie osservate oggi.",
        "🏅 classifica progressione oggi — confronto tra giocatori registrati.",
        "🗓️ Progressione e classifiche supportano oggi, 7, 15 e 30 giorni.",
    ])
    return lines
