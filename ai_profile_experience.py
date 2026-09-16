import random

OFFICIAL_WORLDS = [
    'Starr Park','Retropoli','Bosco incantato','Paese delle caramelle','Nave pirata','Acquario dei mostri marini',
    'Starr Force','Palude degli innamorati','Circo bizzarro','Maniero','Piramide','Giungla','Deposito rottami',
    'Biosfera','Negozio di souvenir','Emporio delle stranezze','Festival musicale','Starr Toon Studios','Isola tropicale',
    'Katana Kingdom','Hub','Fabbrica dei robot','Hotel delle nevi','Cimitero di Mortis','Bazar di Tara','Stunt Show'
]
FANTASY_WORLDS = [
    'cinema IMAX','sala di comando TITANI ABUSIVI','astronave interstellare','osservatorio spaziale',
    'metropoli cyberpunk sotto la pioggia','arena romana','castello monumentale','sala del trono',
    'museo dei trofei','caveau futuristico','citta sommersa','base artica','isola vulcanica',
    'laboratorio segreto','hangar mecha','stadio gremito','red carpet cinematografico','studio televisivo',
    'sala gaming futuristica','nave pirata nella tempesta','foresta mistica','grattacielo sopra le nuvole'
]
CINEMATIC_WORLDS = [
    'set cinematografico fotorealistico al tramonto','metropoli notturna sotto la pioggia','rovine monumentali illuminate da luce volumetrica',
    'campo di battaglia cinematografico con fumo e particelle','hangar industriale fotorealistico','deserto epico durante la golden hour',
    'foresta cinematografica immersa nella nebbia','arena monumentale realistica'
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

def choose_scene(player, category='random'):
    brawler=profile_brawler(player)
    if category=='official': place=random.choice(OFFICIAL_WORLDS); source='Brawl Stars'
    elif category in ('cinema','cinematic'): place=random.choice(CINEMATIC_WORLDS); source='Cinematic Sens GPT'
    elif category=='scifi': place=random.choice(['astronave interstellare','osservatorio spaziale','sala di comando TITANI ABUSIVI','hangar mecha']); source='Fantasia Sens GPT'
    elif category=='epic': place=random.choice(['arena romana','castello monumentale','sala del trono','museo dei trofei']); source='Fantasia Sens GPT'
    elif category=='fantasy': place=random.choice(['foresta mistica','castello monumentale','citta sommersa','isola vulcanica']); source='Fantasia Sens GPT'
    else:
        official=random.random()<0.55; place=random.choice(OFFICIAL_WORLDS if official else FANTASY_WORLDS); source='Brawl Stars' if official else 'Fantasia Sens GPT'
    subject=brawler or 'il Brawler della foto profilo'
    concepts=[f'{subject} osserva le statistiche del giocatore integrate nella scena',f'{subject} e protagonista mentre trofei, Classificata e vittorie diventano elementi fisici e olografici',f'inquadratura cinematografica di {subject}, con statistiche incorporate nell ambiente']
    return {'place':place,'source':source,'brawler':brawler,'brawler_reference':profile_brawler_reference(player),'concept':random.choice(concepts),'category':category}

def build_visual_prompt(player, category='random'):
    scene=choose_scene(player,category); name=player.get('name') or 'Giocatore'; tag=player.get('tag') or ''; identity=scene['brawler'] or 'Brawler identificato dalla reference della foto profilo'
    cinematic = category in ('cinema','cinematic')
    style = (
        "CINEMATIC PHOTOREALISM PRIORITARIO: trasforma il Brawler in una reinterpretazione live-action estremamente realistica, mantenendo IDENTITA, silhouette, colori, costume, accessori e tratti distintivi della reference. Aspetto da 8K photorealistic render e film ad altissimo budget; high-fidelity facial features; anatomia credibile; pelle con pori e micro-texture; barba, baffi o peluria solo se coerenti con il Brawler originale, composti da singoli peli realistici; capelli estremamente realistici composti da migliaia di singole ciocche e fibre, con follicoli e variazioni naturali; piume, pelo, squame, tessuti, pelle, metallo, legno e altri materiali con micro-dettaglio fisicamente plausibile. Per Brawler non umani conserva la loro specie e caratteristiche: non umanizzarli arbitrariamente. Illuminazione cinematografica fisicamente plausibile, global illumination, ray-traced look, volumetric light, realistic reflections, subsurface scattering dove appropriato, profondita di campo da cinema, texture iper-dettagliate. Evita aspetto cartoon, plastica, giocattolo, low-poly o CGI economica. "
        if cinematic else
        "Crea una scena 3D cinematografica, realistica ma fedele a Brawl Stars. "
    )
    return (style+f"Ambientazione: {scene['place']}. Idea narrativa: {scene['concept']}. Profilo: {name} {tag}. Personaggio: {identity}. "
            "REGOLA PRIORITARIA: il Brawler deve restare riconoscibile e fedele al design originale della reference: stesso volto/identita, silhouette, palette, costume, accessori e caratteristiche distintive. Non sostituirlo con un personaggio diverso. La creativita riguarda posa, regia, ambiente, prospettiva, illuminazione e materiali. Integra naturalmente TITANI ABUSIVI nell ambiente. Nome e dati devono poter essere incorporati nella scena, non in una tabella piatta."), scene
