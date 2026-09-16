import random

OFFICIAL_WORLDS = [
    'Starr Park','Retropoli','Bosco incantato','Paese delle caramelle','Nave pirata','Acquario dei mostri marini',
    'Starr Force','Palude degli innamorati','Circo bizzarro','Maniero','Piramide','Giungla','Deposito rottami',
    'Biosfera','Negozio di souvenir','Emporio delle stranezze','Festival musicale','Starr Toon Studios','Isola tropicale',
    'Katana Kingdom','Hub','Fabbrica dei robot','Hotel delle nevi','Cimitero di Mortis','Bazar di Tara','Stunt Show'
]
FANTASY_WORLDS = [
    'sala di comando TITANI ABUSIVI','astronave interstellare','osservatorio spaziale',
    'metropoli cyberpunk sotto la pioggia','arena romana','castello monumentale','sala del trono',
    'museo dei trofei','caveau futuristico','citta sommersa','base artica','isola vulcanica',
    'laboratorio segreto','hangar mecha','stadio gremito','red carpet cinematografico','studio televisivo',
    'sala gaming futuristica','nave pirata nella tempesta','foresta mistica','grattacielo sopra le nuvole'
]
CINEMATIC_WORLDS = [
    'metropoli notturna sotto la pioggia','rovine monumentali illuminate da luce volumetrica',
    'campo di battaglia cinematografico con fumo e particelle','hangar industriale fotorealistico',
    'deserto epico durante la golden hour','foresta cinematografica immersa nella nebbia','arena monumentale realistica',
    'Starr Park reinterpretato come set live-action fotorealistico'
]


def profile_brawler(player):
    for key in ('profile_brawler','icon_brawler','profile_icon_brawler','selected_brawler'):
        value=player.get(key)
        if isinstance(value,dict): value=value.get('name')
        if value: return str(value)
    icon=player.get('icon') or player.get('profile_icon')
    if isinstance(icon,dict):
        for key in ('brawler','brawler_name'):
            if icon.get(key): return str(icon[key])
    return None


def profile_brawler_reference(player):
    for key in ('profile_brawler_image_url','profile_icon_url','icon_url'):
        value=player.get(key)
        if isinstance(value,str) and value.startswith(('http://','https://')): return value
    icon=player.get('icon') or player.get('profile_icon')
    if isinstance(icon,dict):
        for key in ('imageUrl','image_url','url'):
            value=icon.get(key)
            if isinstance(value,str) and value.startswith(('http://','https://')): return value
    return None


def cinematic_identity_rules(brawler):
    """Keep the official Brawler identity while translating only its rendering to reality."""
    name=(brawler or '').casefold().strip()
    special={
        'crow': "Crow deve restare il CORVO ANTROPOMORFO di Brawl Stars: conserva ESATTAMENTE testa e becco stilizzati, occhi, silhouette, proporzioni umanoidi, piumaggio nero/blu, giacca e pugnali della reference. Aggiungi microdettaglio realistico alle piume e ai materiali, ma NON usare anatomia/proporzioni di un corvo reale e NON trasformarlo in uomo o uomo mascherato.",
        'spike': "Spike deve restare ESATTAMENTE la creatura-cactus antropomorfa di Brawl Stars: stessa forma, volto, arti e proporzioni della reference. Rendi fisicamente dettagliata la superficie vegetale, ma NON sostituirlo con un cactus reale o un umano.",
        'rico': "Rico deve restare ESATTAMENTE il robot Brawl Stars della reference: stessa geometria, testa/volto-display, silhouette, colori e componenti. Fotorealismo solo su metallo, vernice, giunture, riflessi e micrograffi; nessun volto umano.",
        'barley': "Barley deve restare ESATTAMENTE il robot Brawl Stars della reference, con identita, testa, corpo e proporzioni originali. Materiali metallici e vetro realistici; nessuna umanizzazione o reinterpretazione anatomica.",
        'tick': "Tick deve restare ESATTAMENTE la creatura robotica/meccanica Brawl Stars della reference, con testa, corpo e arti originali. Fotorealismo dei materiali, non redesign.",
        'otis': "Otis deve conservare ESATTAMENTE natura non umana, silhouette, volto, arti, costume e proporzioni della reference. Materiali plausibili senza nuova anatomia.",
        'squeak': "Squeak deve conservare ESATTAMENTE la creatura gelatinosa Brawl Stars e la forma originale. Fotorealismo del materiale translucido senza trasformarlo in umano o animale.",
        'nani': "Nani deve restare ESATTAMENTE robotica come nella reference. Dettaglio realistico su metallo, ottiche e giunti senza modificare struttura o silhouette.",
        'surge': "Surge deve restare ESATTAMENTE il robot/mecha Brawl Stars della reference: stessa silhouette, armatura, testa e colori. Materiali hard-surface realistici senza redesign umano.",
        '8-bit': "8-Bit deve restare ESATTAMENTE la macchina arcade antropomorfa Brawl Stars della reference: stessa forma, display, arti e proporzioni. Fotorealismo solo dei materiali.",
    }
    if name in special:
        return special[name]
    return (
        "TRADUZIONE IN REALTA, NON REDESIGN: la reference ufficiale e la fonte visiva assoluta. Mantieni specie/natura, anatomia STILIZZATA, volto, silhouette, proporzioni, palette, costume, accessori e segni distintivi esattamente riconoscibili. "
        "Il risultato deve sembrare IL PERSONAGGIO DI BRAWL STARS RESO FISICAMENTE REALE, non una persona/animale/robot reale che gli assomiglia o indossa il suo costume. "
        "Se umano, conserva lineamenti e proporzioni iconiche e rendi realistici pelle/capelli/barba solo se presenti. Se animale o creatura, conserva l'anatomia antropomorfa/stilizzata della reference: NON convertirla nell'anatomia di un animale reale. Se robot/mecha resta interamente meccanico e con la geometria originale. Se vegetale, gelatinoso o soprannaturale conserva quella natura. "
        "NON umanizzare, NON animalizzare, NON cambiare specie, NON reinterpretare il volto, NON alterare silhouette o proporzioni, NON creare un cosplay e NON sostituire il Brawler con un equivalente realistico generico."
    )


def choose_scene(player, category='random'):
    brawler=profile_brawler(player)
    if category=='official': place=random.choice(OFFICIAL_WORLDS); source='Brawl Stars'
    elif category=='cinema': place='cinema IMAX'; source='Location cinema Sens GPT'
    elif category=='cinematic': place=random.choice(CINEMATIC_WORLDS); source='Cinematic Sens GPT'
    elif category=='scifi': place=random.choice(['astronave interstellare','osservatorio spaziale','sala di comando TITANI ABUSIVI','hangar mecha']); source='Fantasia Sens GPT'
    elif category=='epic': place=random.choice(['arena romana','castello monumentale','sala del trono','museo dei trofei']); source='Fantasia Sens GPT'
    elif category=='fantasy': place=random.choice(['foresta mistica','castello monumentale','citta sommersa','isola vulcanica']); source='Fantasia Sens GPT'
    else:
        official=random.random()<0.55
        place=random.choice(OFFICIAL_WORLDS if official else FANTASY_WORLDS)
        source='Brawl Stars' if official else 'Fantasia Sens GPT'
    subject=brawler or 'il Brawler della foto profilo'
    concepts=[
        f'{subject} osserva le statistiche del giocatore integrate fisicamente nell architettura e negli oggetti della scena',
        f'{subject} e protagonista mentre trofei, Classificata e vittorie fanno parte dell ambiente senza pannelli UI',
        f'inquadratura cinematografica di {subject}, con statistiche fuse nella scenografia e mai sovrapposte in box'
    ]
    return {'place':place,'source':source,'brawler':brawler,'brawler_reference':profile_brawler_reference(player),'concept':random.choice(concepts),'category':category}


def build_visual_prompt(player, category='random'):
    scene=choose_scene(player,category)
    name=player.get('name') or 'Giocatore'; tag=player.get('tag') or ''
    identity=scene['brawler'] or 'Brawler identificato dalla reference della foto profilo'
    if category=='cinematic':
        style=(
            "MODALITA CINEMATIC PHOTOREALISTIC: obiettivo visivo = 'the exact Brawl Stars character brought into the real world', NON 'a real animal/person dressed like the Brawler'. 8K photorealistic render, produzione live-action ad altissimo budget, physically plausible textures, realistic global illumination, volumetric light, cinematic depth of field, realistic reflections e micro-dettagli. "
            "PRIORITA ASSOLUTA ALLA REFERENCE: prima copia identita, silhouette, volto, anatomia stilizzata, proporzioni, costume e accessori; soltanto dopo applica materiali e luce fotorealistici. Se fotorealismo e fedelta entrano in conflitto, VINCE SEMPRE LA FEDELTA ALLA REFERENCE. "
            "Capelli/peli/barba SOLO quando realmente presenti nel design. Materiali coerenti con il soggetto: piume, pelo, squame, pelle, tessuti, metallo, vegetazione, gel o superfici soprannaturali, ma senza cambiare la forma originale. "
            + cinematic_identity_rules(scene['brawler']) + " "
            "Evita cartoon generico, giocattolo, low-poly, CGI economica, cosplay, animale reale, persona reale sostitutiva e redesign. "
        )
    elif category=='cinema':
        style=("MODALITA AL CINEMA: ambientazione obbligatoria cinema IMAX/sala cinematografica. Questa e una LOCATION, NON la modalita Cinematic fotorealistica. Mantieni la normale resa 3D cinematografica fedele a Brawl Stars. ")
    else:
        style="Crea una scena 3D cinematografica, realistica ma fedele a Brawl Stars. "
    return (
        style+f"Ambientazione: {scene['place']}. Idea narrativa: {scene['concept']}. Profilo: {name} {tag}. Personaggio: {identity}. "
        "REGOLA PRIORITARIA BRAWLER: deve essere immediatamente riconoscibile come lo STESSO personaggio della reference, non una reinterpretazione. Mantieni identita, silhouette, anatomia/proporzioni stilizzate, palette, costume, accessori e caratteristiche distintive. "
        "REGOLA TESTI E STATISTICHE: VIETATI box, card, pannelli UI, targhette traslucide, rettangoli arrotondati, cornici, HUD sospesi e blocchi di testo sovrapposti. Nome, tag, trofei, vittorie, Classificata e altri dati devono sembrare parte fisica o luminosa della scenografia: incisioni su pietra/metallo, insegne, pareti, gradini, pavimento, stendardi, ologrammi diegetici o altri elementi coerenti con il luogo. Distribuisci i dati nello scenario senza coprire il Brawler e lascia margine di sicurezza dai quattro bordi affinche nessun testo venga tagliato. "
        "Integra naturalmente TITANI ABUSIVI nell ambiente usando il logo di riferimento come identita visiva: niente badge bianco, watermark o logo galleggiante; deve apparire su un elemento fisico coerente della scena. Mantieni il Brawler come protagonista."
    ), scene
