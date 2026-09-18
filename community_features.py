        if rarity:
            return labels.get(rarity, rarity.replace("_", " ").title())
        tid = str((row.get("source_payload") or {}).get("tid") or "").upper()
        conf = str((row.get("source_payload") or {}).get("conf") or "").upper()
        if "TRUE_GOLD" in tid or conf.endswith("GOLD") or conf.endswith("_GOLD"):
            return "Oro 24 carati"
        if "TRUE_SILVER" in tid or conf.endswith("SILVER") or conf.endswith("_SILVER"):
            return "Argento"
        if "PROPASS_PROGRESSION" in tid:
            return "Brawl Pass"
        return "Speciali"

    def skin_account_text(self, registered_user, brawler_name=None, rarity=None):
        if not registered_user or not registered_user.get("player_tag"):
            return "Devi prima registrare il tuo tag Brawl Stars."
        try:
            # PostgREST defaults to 1,000 rows. Fetch the complete verified catalogue in pages.
            catalog = []
            page_size = 1000
            offset = 0
            while True:
                page = self._get("skins_catalog", {
                    "select": "external_id,name_en,name_it,rarity,brawler_name,source_payload",
                    "verification_status": "eq.structured_verified",
                    # Ghost Buffies are cosmetic Buddy items, not Brawler skins. Keep them in
                    # the master catalogue but exclude them semantically from Skin Account.
                    "external_id": "not.in.(29001472,29001473,29001831,29001832,29001833,29001834,29001835,29001836)",
                    "order": "brawler_name.asc,name_en.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                })
                catalog.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
            if not catalog:
                return "Il catalogo skin non è disponibile in questo momento."
            owned_ids = self._official_owned_skin_ids(registered_user["player_tag"])
            # Never count the default Brawler appearance: it is absent from our canonical skin catalog.
            rows = list(catalog)
            if brawler_name: