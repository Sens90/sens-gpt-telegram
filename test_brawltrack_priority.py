import app


def test_brawltrack_is_primary_meta_source():
    assert app.meta_source_priority("https://brawltrack.app/maps/15000300") == 0
    assert app.meta_source_priority("https://brawltrack.app/pro/maps/Ring%20Of%20Fire") == 0
    assert app.meta_source_priority("https://www.brawlplanet.com/it/maps/foo") > 0


def test_game_context_stays_separate():
    assert app.get_game_context("meta trofei") == "ladder"
    assert app.get_game_context("meta classificata") == "ranked"
    assert app.get_game_context("meta attuale") == "both"
