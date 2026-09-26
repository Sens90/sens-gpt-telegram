-- Store only verified per-account ownership IDs; category totals alone cannot
-- reconstruct the owned and missing names of individual Brawler skins.
create table if not exists public.skin_owned_ids_latest (
    player_tag text primary key,
    observed_at timestamptz not null default now(),
    owned_skin_ids jsonb not null default '[]'::jsonb
);

alter table public.skin_owned_ids_latest enable row level security;
revoke all on public.skin_owned_ids_latest from anon, authenticated;
grant select, insert, update on public.skin_owned_ids_latest to service_role;
