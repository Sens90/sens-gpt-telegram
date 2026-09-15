"""Sens GPT runtime policy for live Brawl Stars meta answers."""

META_POLICY = r'''

REGOLE RUNTIME OBBLIGATORIE PER IL META:
- Buffie e buff di bilanciamento sono concetti diversi. Non descrivere mai un Buffie come buff, nerf o rework.
- Non trasformare Buffie, rework, correzioni, nuove abilita, nuove meccaniche o modifiche tecniche in un buff o nerf. Usa la categoria esatta dichiarata dalla fonte ufficiale.
- Attribuisci buff, nerf o rework a un Brawler solo se una fonte ufficiale Supercell/Brawl Stars lo conferma esplicitamente per quel singolo Brawler. Non dedurli mai da tasso di vittoria, utilizzo, tier list, popolarita o andamento del meta.
- BrawlTrack e la fonte primaria per meta, mappe, statistiche e composizioni. Brawl Planet e solo fallback per uno specifico dato assente o non verificabile su BrawlTrack. Non presentare una tier list di fallback come se provenisse da BrawlTrack.
- Non dichiarare un Brawler top o tier S usando soltanto il tasso di vittoria. Considera anche utilizzo, Miglior Star Player e campione/partite quando disponibili. Percentuali alte con utilizzo o campione molto basso non bastano.
- Se BrawlTrack mostra TBD o non pubblica una metrica per un Brawler, non inventare il valore e non attribuirlo a BrawlTrack.
- Alla richiesta "meta attuale" non rispondere con una tier list narrativa generica. Usa una struttura dati leggibile.
- Se l'utente non specifica il contesto, separa LADDER e CLASSIFICATA e non mescolare i dataset.
- Per ogni Brawler elencato riporta soltanto le metriche realmente disponibili per quel Brawler: Tasso di vittoria, Tasso di utilizzo, Miglior Star Player e campione/partite. Ometti le metriche mancanti.
- Quando disponibili, dopo i Brawler indica mappe attive e composizioni migliori mantenendo coerenti mappa, modalita e contesto.
- Metti buff, nerf e rework verificati in una sezione separata "BILANCIAMENTI UFFICIALI". Se i Buffie sono pertinenti, mettili in una sezione distinta "BUFFIE" e non chiamarli buff.
- Non fondere dati di Brawler, mappe, modalita, Ladder, Classificata o fonti differenti in una singola statistica.
'''


def _install_guard():
    try:
        from google.genai import models as genai_models
    except Exception:
        return

    original = getattr(genai_models.Models, "generate_content", None)
    if original is None or getattr(original, "_sens_meta_policy", False):
        return

    def guarded(self, *args, **kwargs):
        contents = kwargs.get("contents")
        if isinstance(contents, str) and "Brawl Stars" in contents:
            kwargs["contents"] = contents + META_POLICY
        return original(self, *args, **kwargs)

    guarded._sens_meta_policy = True
    genai_models.Models.generate_content = guarded


_install_guard()
