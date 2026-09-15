# sens-gpt-telegram

## Tracking automatico giocatori

Il servizio aggiorna automaticamente tutti i tag registrati in Supabase. Ogni
controllo salva trofei, Ranked attuale, massima stagionale e massima carriera;
le variazioni Ranked vengono conservate in `ranked_history`.

1. Eseguire `supabase_community_schema.sql` nel SQL Editor di Supabase.
2. Impostare facoltativamente `TRACKING_INTERVAL_MINUTES` su Render (default 15,
   minimo 5).
3. Distribuire il branch `main`. I nuovi tag entrano nel monitor con
   `registrami #TAG`.
