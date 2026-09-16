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
    """Prefer the Brawler represented by the player's selected profile icon when the upstream source exposes it."""
    for key in ('profile_brawler','icon_brawler','profile_icon_brawler','selected_brawler'):
        value=player.get(key)
        if isinstance(value,dict): value=value.get('name')
        if value: return str(value)
    icon=player.get('icon') or player.get('profile_icon')
    if isinstance(icon,dict):
        for key in ('brawler','brawler_name','name'):
            if icon.get(key): return str(icon[key])
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
    subject=brawler or 'il Brawler associato alla foto profilo del giocatore, se identificabile'
    concepts=[
        f'{subject} osserva le statistiche del giocatore su enormi schermi integrati nella scena',
        f'{subject} e protagonista della scena mentre trofei, Classificata e vittorie vengono rappresentati come elementi fisici e olografici',
        f'inquadratura cinematografica di {subject}, con le statistiche raccontate attraverso cartelloni, monitor e oggetti dell ambiente',
    ]
    return {'place':place,'source':source,'brawler':brawler,'concept':random.choice(concepts)}

def build_visual_prompt(player, category='random'):
    scene=choose_scene(player,category)
    name=player.get('name') or 'Giocatore'; tag=player.get('tag') or ''
    return (f"Crea una scena 3D cinematografica, realistica ma fedele all identita visiva di Brawl Stars. "
            f"Ambientazione: {scene['place']}. Idea narrativa: {scene['concept']}. "
            f"Profilo: {name} {tag}. Integra naturalmente il marchio TITANI ABUSIVI nell ambiente. "
            "Illuminazione da film, profondita, volumetric light, materiali dettagliati, composizione dinamica, niente tabella piatta. "
            "NON inventare o disegnare numeri/statistiche: saranno sovrapposti dal renderer dopo la generazione. "
            "Lascia aree leggibili nella composizione per i dati reali."), scene
