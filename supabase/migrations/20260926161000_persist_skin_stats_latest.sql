-- Keep the latest successfully measured Stats totals separate from the
-- older collection snapshots, which use a different ownership source.
create table if not exists public.skin_stats_latest (
    player_tag text primary key,
    observed_at timestamptz not null default now(),
    skins_owned integer not null check (skins_owned >= 0),
    skin_rarity_counts jsonb not null default '{}'::jsonb
);

alter table public.skin_stats_latest enable row level security;
revoke all on public.skin_stats_latest from anon, authenticated;
grant select, insert, update on public.skin_stats_latest to service_role;
