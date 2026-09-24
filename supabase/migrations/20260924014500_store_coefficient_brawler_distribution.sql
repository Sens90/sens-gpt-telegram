alter table public.coefficient_history
    add column if not exists brawler_trophy_values jsonb;

comment on column public.coefficient_history.brawler_trophy_values is
    'Numeric per-Brawler trophy distribution used to audit coefficient bands; no profile payload is stored.';
