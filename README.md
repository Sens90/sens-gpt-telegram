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

## Reclutamento dal sito

Il pulsante WordPress **Unisciti a noi** deve puntare a:
`https://t.me/SensGPT_TitaniAbusiviBot?start=reclutamento`.

Il percorso privato accetta anche utenti non registrati. Richiede tag Brawl Stars
e club (Titani, Tamarri, Tornadi o Talenti), recupera il profilo e salva una riga
in `community_recruitments`. Il codice `CAND-<id>` deriva dall'ID persistente;
una candidatura pendente dello stesso ID Telegram viene riutilizzata.

`RECRUITMENT_COMMUNITY_CHAT_ID` identifica la Community destinataria. Se manca,
il bot usa l'unico chat_id negativo presente in `community_settings`; con più
gruppi richiede configurazione esplicita. Nessuna nuova tabella è necessaria.

Gli amministratori della Community usano `candidature` per ricevere l'elenco in
privato. Dopo aver verificato il possesso del profilo possono usare
`approva CAND-17 verificato` o `rifiuta CAND-17`. Il bot salva la decisione e
la comunica al candidato. L'invito approvato scade dopo 24 ore e genera una
richiesta di ingresso: il bot approva solo l'ID Telegram associato alla candidatura,
poi revoca il link. Servono permessi Telegram per creare inviti e approvare
richieste. Dopo l'ingresso il candidato collega il tag con `registrami #TAG`.

In caso di consegna fallita, la decisione resta in uno stato intermedio e lo staff
può ripetere il comando senza perdere la candidatura. La verifica del possesso
del profilo resta responsabilità dello staff: il tag da solo non ne è una prova.

Verifica locale: `python -m unittest test_recruitment_flow -v`.
