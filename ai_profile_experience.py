import random

OFFICIAL_WORLDS = ['Starr Park','Retropoli','Bosco incantato','Paese delle caramelle','Nave pirata','Acquario dei mostri marini','Starr Force','Palude degli innamorati','Circo bizzarro','Maniero','Piramide','Giungla','Deposito rottami','Biosfera','Negozio di souvenir','Emporio delle stranezze','Festival musicale','Starr Toon Studios','Isola tropicale','Katana Kingdom','Hub','Fabbrica dei robot','Hotel delle nevi','Cimitero di Mortis','Bazar di Tara','Stunt Show']

def profile_brawler(player):
    for key in ('profile_brawler','icon_brawler','profile_icon_brawler','selected_brawler'):
        value=player.get(key)
        if isinstance(value,dict): value=value.get('name')
        if value:return str(value)
    icon=player.get('icon') or player.get('profile_icon')
    if isinstance(icon,dict):
        for key in ('brawler','brawler_name'):
            if icon.get(key):return str(icon[key])
    return None

def profile_brawler_reference(player):
    for key in ('profile_brawler_image_url','profile_icon_url','icon_url'):
        value=player.get(key)
        if isinstance(value,str) and value.startswith(('http://','https://')):return value
    icon=player.get('icon') or player.get('profile_icon')
    if isinstance(icon,dict):
        for key in ('imageUrl','image_url','url'):
            value=icon.get(key)
            if isinstance(value,str) and value.startswith(('http://','https://')):return value
    return None

def cinematic_identity_rules(brawler):
    name=(brawler or '').casefold().strip()
    special={
        'crow':"Crow deve restare il CORVO ANTROPOMORFO di Brawl Stars: conserva ESATTAMENTE testa e becco stilizzati, occhi, silhouette, proporzioni umanoidi, piumaggio nero/blu, giacca e pugnali della reference. Aggiungi microdettaglio realistico alle piume e ai materiali, ma NON usare anatomia/proporzioni di un corvo reale e NON trasformarlo in uomo o uomo mascherato.",
        'spike':"Spike deve restare ESATTAMENTE la creatura-cactus antropomorfa di Brawl Stars: stessa forma, volto, arti e proporzioni della reference. Rendi fisicamente dettagliata la superficie vegetale, ma NON sostituirlo con un cactus reale o un umano.",
        'rico':"Rico deve restare ESATTAMENTE il robot Brawl Stars della reference: stessa geometria, testa/volto-display, silhouette, colori e componenti. Fotorealismo solo su metallo, vernice, giunture, riflessi e micrograffi; nessun volto umano.",
        'barley':"Barley deve restare ESATTAMENTE il robot Brawl Stars della reference, con identita, testa, corpo e proporzioni originali. Materiali metallici e vetro realistici; nessuna umanizzazione.",
        'tick':"Tick deve restare ESATTAMENTE la creatura robotica/meccanica Brawl Stars della reference. Fotorealismo dei materiali, non redesign.",
        'otis':"Otis deve conservare ESATTAMENTE natura non umana, silhouette, volto, arti, costume e proporzioni della reference.",
        'squeak':"Squeak deve conservare ESATTAMENTE la creatura gelatinosa Brawl Stars e la forma originale, senza trasformarlo in umano o animale.",
        'nani':"Nani deve restare ESATTAMENTE robotica come nella reference. Dettaglio realistico senza modificare struttura o silhouette.",
        'surge':"Surge deve restare ESATTAMENTE il robot/mecha Brawl Stars della reference: stessa silhouette, armatura, testa e colori.",
        '8-bit':"8-Bit deve restare ESATTAMENTE la macchina arcade antropomorfa Brawl Stars della reference: stessa forma, display, arti e proporzioni."
    }
    if name in special:return special[name]
    return "TRADUZIONE IN REALTA, NON REDESIGN: la reference ufficiale e la fonte visiva assoluta. Mantieni specie/natura, anatomia STILIZZATA, volto, silhouette, proporzioni, palette, costume, accessori e segni distintivi. Il risultato deve essere IL PERSONAGGIO DI BRAWL STARS RESO FISICAMENTE REALE, non una persona/animale/robot reale che gli assomiglia. NON umanizzare, NON animalizzare, NON cambiare specie, NON reinterpretare il volto, NON alterare silhouette o proporzioni e NON creare un cosplay."

def choose_scene(player,category='random',custom_environment=None):
    brawler=profile_brawler(player)
    custom=(custom_environment or player.get('ai_custom_environment') or '').strip()
    if custom: place=custom[:120]; source='Ambientazione richiesta dall utente'
    elif category=='cinema': place='cinema IMAX'; source='Location cinema Sens GPT'
    else: place=random.choice(OFFICIAL_WORLDS); source='Brawl Stars'
    subject=brawler or 'il Brawler della foto profilo'
    concepts=[f'{subject} osserva le statistiche integrate fisicamente nell architettura',f'{subject} e protagonista mentre trofei, Classificata e vittorie fanno parte dell ambiente senza pannelli UI',f'inquadratura cinematografica di {subject}, con statistiche fuse nella scenografia']
    return {'place':place,'source':source,'brawler':brawler,'brawler_reference':profile_brawler_reference(player),'concept':random.choice(concepts),'category':category,'custom_environment':custom or None}

def build_visual_prompt(player,category='random',custom_environment=None):
    scene=choose_scene(player,category,custom_environment); name=player.get('name') or 'Giocatore'; tag=player.get('tag') or ''; identity=scene['brawler'] or 'Brawler identificato dalla reference'
    if category=='cinematic': style="MODALITA CINEMATIC PHOTOREALISTIC: the exact Brawl Stars character brought into the real world, NON un animale/persona reale vestito come il Brawler. 8K photorealistic render. PRIORITA ASSOLUTA ALLA REFERENCE: copia prima identita, silhouette, volto, anatomia stilizzata, proporzioni, costume e accessori; solo dopo applica materiali e luce fotorealistici. Se realismo e fedelta entrano in conflitto, VINCE LA REFERENCE. "+cinematic_identity_rules(scene['brawler'])+" "
    elif category=='cinema': style="MODALITA AL CINEMA: normale resa 3D cinematografica fedele a Brawl Stars. "
    else: style="Crea una scena 3D cinematografica realistica ma fedele a Brawl Stars. "
    return (style+f"Ambientazione OBBLIGATORIA: {scene['place']}. Idea narrativa: {scene['concept']}. Profilo: {name} {tag}. Personaggio: {identity}. REGOLA BRAWLER: stesso personaggio della reference, non reinterpretarlo. REGOLA TESTI: VIETATI box, card, pannelli UI, targhette traslucide, rettangoli arrotondati, cornici e HUD sospesi. Integra nome, tag e statistiche fisicamente nella scenografia, lontani dai bordi. Integra TITANI ABUSIVI nell ambiente usando il logo di riferimento, mai come watermark o badge. Mantieni il Brawler protagonista."),scene
