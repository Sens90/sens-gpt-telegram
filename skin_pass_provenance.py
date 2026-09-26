"""Historically verified Brawl Pass skin names from Supercell release notes.

The game CSV provides present rarity and gem price, not the original unlock
source. Keep provenance separate from acquisition_type: a formerly Pass skin
can later become available through other means. This list is conservative and
can be extended as older seasons are checked against official release notes.
"""
import re

_OFFICIAL_BRAWL_PASS_SEASONS = {
    # https://supercell.com/en/games/brawlstars/blog/release-notes/starrtoon-patch-notes/
    "2024 Starr Toon / Year of the Dragon": (
        "Pinku Pawlette", "Kiiro Pawlette", "Midori Pawlette",
        "KitBoxer", "KitBoxer Goldpaw", "KitBoxer Darkpaw",
    ),
    # https://supercell.com/en/games/brawlstars/blog/release-notes/patch-notes-ranked-update/
    "2024 Sands of Time / Ragnarok": (
        "Shelly Dancer", "Shelly Dancer Iris", "Shelly Dancer Dahlia",
        "Fenrir Buzz", "Mørk Fenrir Buzz", "Eldr Fenrir Buzz",
    ),
    # https://supercell.com/en/games/brawlstars/blog/release-notes/patch-notes-godzilla-attacks-brawl/
    "2024 Godzilla / CyberBrawl": (
        "Mecha-Tick Ghidorah", "Mecha-Tick Ghidorah Dark", "Mecha-Tick Ghidorah Light",
        "Hacker Brock", "Master Hacker Brock", "RGB Hacker Brock",
    ),
    # https://supercell.com/en/games/brawlstars/blog/game-updates/patch-notes-angels-vs-demons/
    # https://supercell.com/en/games/brawlstars/blog/release-notes/patch-notes-toy-story/
    "2024 Angels & Demons / Starr Toon": (
        "Angel Larry & Lawrie", "Radiant Larry & Lawrie", "Dark Angel Larry & Lawrie",
        "Bizarre Maisie", "Requiem Maisie", "Platinum Maisie",
    ),
    # https://supercell.com/en/games/brawlstars/blog/release-notes/release-notes-ranked-rework-lumi-finx-2/
    "2025 February": (
        "Mummified Frank", "Charcoal Frank", "Papyrus Frank",
        "Justice Smasher Bibi", "Injustice Smasher Bibi", "Virtue Smasher Bibi",
    ),
    # https://supercell.com/en/games/brawlstars/blog/release-notes/release-notes-september-2025/
    "2025 September": (
        "Streetwear Emz", "Hypebeast Emz", "Techwear Emz",
        "Plague Doctor Crow", "Death’s Door Crow", "Old Hunter Crow",
    ),
    # https://supercell.com/en/games/brawlstars/blog/game-updates/release-notes-october-2025/
    "2025 October": (
        "Eleven Lumi", "Retro Eleven Lumi", "Thrilled Eleven Lumi",
        "Mecha Mandy", "Wasp Mecha Mandy", "Shade Mecha Mandy",
    ),
    # https://supercell.com/en/games/brawlstars/blog/release-notes/release-notes-february-2026/
    "2026 Sands of Time / Dragons and Faeries": (
        "Sandstalker Lily", "Sandwalker Lily", "Night Sands Lily",
        "Sultan Cordelius", "Heated Sultan Cordelius", "Cooled Sultan Cordelius",
        "Faerie Bonnie", "Dark Faerie Bonnie", "Bright Faerie Bonnie",
        "Dragon Griff", "Greedy Griff", "Hoarder Griff",
    ),
    # https://supercell.com/en/games/brawlstars/blog/release-notes/release-notes-april-2026/
    "2026 Starr Patrol / Brawl Strikers": (
        "Starr Patrol Spike", "Starr Scout Spike", "Starr Guardian Spike",
        "Void Colette", "White Dwarf Colette", "Black Hole Colette",
        "Super Ball Gene", "Keeper Gene", "Score Stopper Gene",
        "Super Ball Carl", "Striker Carl", "Kicker Carl",
    ),
    # https://supercell.com/en/games/brawlstars/blog/release-notes/release-notes-june-2026/
    "2026 NanoNoodles / Windstock": (
        "Master Mico", "Fire Master Mico", "Earth Master Mico",
        "Cyberpunk Jae-Yong", "Nano Jae-Yong", "Choom Jae-Yong",
        "Biotech Byron", "Unearthed Byron", "Leaf Master Byron",
        "Retrofuture Rosa", "Renewables Rosa", "Sci-Fi Rosa",
    ),
    # https://supercell.com/en/games/brawlstars/blog/release-notes/release-notes-august-2026/
    "2026 Royal Academy / Brawl-O-Ween": (
        "Head Coach Hank", "Trainer Hank", "Team Captain Hank",
        "Principal Nani", "Head Teacher Nani", "Queen Nani",
        "Fortune Teller P", "Mr. Psychic", "Mystic P",
        "Cursed Shade", "Creepy Shade", "Jolly Ghost Shade",
    ),
}

def _key(name):
    # Some game localization fields contain a literal \\n inside the skin name.
    return re.sub(r"\s+", " ", str(name or "").replace("\\n", " ").replace("’", "'")).strip().casefold()


BRAWL_PASS_VERIFIED_NAMES = frozenset(
    _key(name) for season in _OFFICIAL_BRAWL_PASS_SEASONS.values() for name in season
)

# Release-note labels differ from the final game localization. The game's
# skin TID ties these exact IDs to the names used in the official notes.
BRAWL_PASS_VERIFIED_ALIASES = frozenset({
    "29000864",  # Master Hacker Brock -> GOLD HACKER BROCK
    "29000993", "29001015", "29001016",  # Bizarre / Requiem / Platinum Maisie
    "29001388", "29001389",  # Wasp / Shade Mecha Mandy
})


def is_verified_brawl_pass_skin(row):
    return (str(row.get("external_id")) in BRAWL_PASS_VERIFIED_ALIASES
            or _key(row.get("name_en")) in BRAWL_PASS_VERIFIED_NAMES)
