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
    """Conservative per-Brawler realism rules. The reference remains authoritative.

    Named profiles handle Brawlers whose nature is especially easy for an image model
    to distort. Every other current/future Brawler receives a strict reference-driven
    fallback instead of being freely humanized.
    """
    name=(brawler or '').casefold().strip()
    special={
        'crow': "Crow deve restare un CORVO ANTROPOMORFO: becco, testa aviaria, piumaggio nero/blu, corporatura, occhi, giacca e pugnali coerenti con la reference. Piume fotorealistiche a singole fibre e microtexture del becco. NON trasformarlo in un uomo, in un uomo mascherato o in un corvo animale normale.",
        'spike': "Spike deve restare una creatura-cactus antropomorfa con forma, volto e proporzioni della reference. Rendi realistica la superficie vegetale con spine, pori e fibre del cactus; NON trasformarlo in un umano o in un cactus ordinario.",
        'rico': "Rico deve restare un robot, con geometria, volto/display, colori e componenti della reference. Fotorealismo su metallo, vernice, giunture, riflessi e micrograffi; nessun volto umano.",
        'barley': "Barley deve restare un robot con identita e proporzioni della reference. Materiali metallici e vetro realistici, usura fisica credibile; nessuna umanizzazione.",
        'tick': "Tick deve restare una creatura robotica/meccanica conforme alla reference, con testa, corpo e arti riconoscibili. Fotorealismo meccanico, non umano.",
        'otis': "Otis deve conservare integralmente la sua natura non umana e la silhouette della reference. Rendi fisicamente plausibili materiali e superfici senza aggiungere anatomia umana.",
        'squeak': "Squeak deve conservare la sua natura gelatinosa/organica e la forma originale. Fotorealismo dei materiali translucidi e umidi senza trasformarlo in un umano o animale diverso.",
        'nani': "Nani deve restare robotica e identica nella struttura fondamentale alla reference. Dettaglio fotorealistico su metallo, ottiche, giunti e superfici; nessuna umanizzazione.",
        'surge': "Surge deve restare un robot/mecha con silhouette, armatura e colori originali. Materiali hard-surface fotorealistici; nessun volto o corpo umano aggiunto.",
        '8-bit': "8-Bit deve restare una macchina arcade antropomorfa con forma e display originali. Fotorealismo su plastica, vetro, metallo e display; nessuna umanizzazione.",
    }
    if name in special:
        return special[name]
    return ("PROFILO REALISMO SPECIFICO DEL BRAWLER: usa la reference ufficiale come autorita assoluta per specie/natura, anatomia, volto, silhouette, proporzioni, palette, costume, accessori e segni distintivi. "
            "Se e umano, mantieni esattamente identita e caratteristiche e rendi realistici pelle, occhi, capelli e barba solo dove presenti. Se e animale/creatura, conserva quella specie e anatomia stilizzata rendendo realistiche piume/pelo/pelle/squame/materiali. Se e robot/mecha, resta interamente meccanico. Se e vegetale, gelatinoso o soprannaturale, conserva quella natura. NON umanizzare, NON cambiare specie e NON sostituire il Brawler con un equivalente realistico generico.")


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
        f'{subject} osserva le statistiche del giocatore integrate nella scena',
        f'{subject} e protagonista mentre trofei, Classificata e vittorie diventano elementi fisici e olografici',
        f'inquadratura cinematografica di {subject}, con statistiche incorporate nell ambiente'
    ]
    return {'place':place,'source':source,'brawler':brawler,'brawler_reference':profile_brawler_reference(player),'concept':random.choice(concepts),'category':category}


def build_visual_prompt(player, category='random'):
    scene=choose_scene(player,category)
    name=player.get('name') or 'Giocatore'; tag=player.get('tag') or ''
    identity=scene['brawler'] or 'Brawler identificato dalla reference della foto profilo'
    if category=='cinematic':
        style=(
            "MODALITA CINEMATIC PHOTOREALISTIC: 8K photorealistic render, produzione live-action ad altissimo budget, hyper-detailed physically plausible textures, high-fidelity facial features quando applicabili, realistic global illumination, volumetric light, cinematic depth of field, realistic reflections e micro-dettagli. "
            "Capelli/peli/barba SOLO quando realmente presenti nel design: migliaia di singole fibre e ciocche, follicoli e variazioni naturali. Materiali coerenti con il soggetto: piume individuali, pelo, squame, pelle, tessuti, metallo, legno, vegetazione, gel o superfici soprannaturali. "
            + cinematic_identity_rules(scene['brawler']) + " "
            "Il fotorealismo deve cambiare SOLO la resa fisica dei materiali, texture e illuminazione: NON l'identita o la natura del Brawler. Evita cartoon generico, plastica, giocattolo, low-poly e CGI economica. "
        )
    elif category=='cinema':
        style=("MODALITA AL CINEMA: ambientazione obbligatoria cinema IMAX/sala cinematografica. Questa e una LOCATION, NON la modalita Cinematic fotorealistica. Mantieni la normale resa 3D cinematografica fedele a Brawl Stars. ")
    else:
        style="Crea una scena 3D cinematografica, realistica ma fedele a Brawl Stars. "
    return (style+f"Ambientazione: {scene['place']}. Idea narrativa: {scene['concept']}. Profilo: {name} {tag}. Personaggio: {identity}. "
            "REGOLA PRIORITARIA: il Brawler deve restare riconoscibile e fedele al design originale della reference: stessa identita, silhouette, palette, costume, accessori e caratteristiche distintive. Non sostituirlo con un personaggio diverso. "
            "La creativita riguarda posa, regia, ambiente, prospettiva, illuminazione e materiali. Integra naturalmente TITANI ABUSIVI nell ambiente. Nome e dati devono essere incorporati nella scena, non in una tabella piatta."), scene
