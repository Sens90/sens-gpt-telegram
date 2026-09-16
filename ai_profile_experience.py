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

def profile_brawler(player):
    """Return only a Brawler explicitly associated with the selected profile icon."""
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
    """Find the strongest available visual reference for the selected profile Brawler."""
    for key in ('profile_brawler_image_url','profile_icon_url','icon_url'):
        value=player.get(key)
        if isinstance(value,str) and value.startswith(('http://','https://')):
            return value
    icon=player.get('icon') or player.get('profile_icon')
    if isinstance(icon,dict):
        for key in ('imageUrl','image_url','url'):
            value=icon.get(key)
            if isinstance(value,str) and value.startswith(('http://','https://')):
                return value
    return None

def choose_scene(player, category='random'):
    brawler=profile_brawler(player)
    if category=='official': place=random.choice(OFFICIAL_WORLDS); source='Brawl Stars'
    elif category=='cinema': place='cinema IMAX'; source='Fantasia Sens GPT'
    elif category=='scifi': place=random.choice(['astronave interstellare','osservatorio spaziale','sala di comando TITANI ABUSIVI','hangar mecha']); source='Fantasia Sens GPT'
    elif category=='epic': place=random.choice(['arena romana','castello monumentale','sala del trono','museo dei trofei']); source='Fantasia Sens GPT'
    else:
        official=random.random()<0.55
        place=random.choice(OFFICIAL_WORLDS if official else FANTASY_WORLDS); source='Brawl Stars' if official else 'Fantasia Sens GPT'
    subject=brawler or 'il Brawler della foto profilo'
    concepts=[
        f'{subject} osserva le statistiche del giocatore su enormi schermi integrati nella scena',
        f'{subject} e protagonista mentre trofei, Classificata e vittorie sono rappresentati da elementi fisici e olografici',
        f'inquadratura cinematografica di {subject}, con statistiche raccontate attraverso cartelloni, monitor e oggetti dell ambiente',
    ]
    return {'place':place,'source':source,'brawler':brawler,'brawler_reference':profile_brawler_reference(player),'concept':random.choice(concepts)}

def build_visual_prompt(player, category='random'):
    scene=choose_scene(player,category)
    name=player.get('name') or 'Giocatore'; tag=player.get('tag') or ''
    identity = scene['brawler'] or 'Brawler identificato dalla reference della foto profilo'
    return (f"Crea una scena 3D cinematografica, realistica ma fedele a Brawl Stars. Ambientazione: {scene['place']}. "
            f"Idea narrativa: {scene['concept']}. Profilo: {name} {tag}. Personaggio: {identity}. "
            "REGOLA PRIORITARIA: il Brawler deve rimanere il piu fedele possibile al design originale della reference: "
            "stesso volto, silhouette, colori, costume, accessori e caratteristiche distintive. Non sostituirlo con un personaggio simile e non ridisegnarlo liberamente. "
            "La creativita deve riguardare posa, regia, ambiente, prospettiva, illuminazione ed effetti, non l identita del Brawler. "
            "Integra naturalmente il marchio TITANI ABUSIVI nell ambiente. Illuminazione da film, profondita, volumetric light, materiali dettagliati, composizione dinamica, niente tabella piatta. "
            "NON inventare o disegnare numeri/statistiche: saranno sovrapposti dal renderer dopo la generazione. Lascia aree leggibili nella composizione per i dati reali."), scene
