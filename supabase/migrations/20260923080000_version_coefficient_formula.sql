alter table public.coefficient_history
  add column if not exists formula_version smallint not null default 1;

create or replace function public.coefficient_progression_rows(
  p_player_tags text[], p_days integer default null
)
returns table(
  player_tag text, player_name text, club_name text,
  coefficient_value integer, coefficient numeric,
  baseline_value integer, progression_value integer,
  recorded_at timestamptz
)
language sql stable set search_path = ''
as $$
  with requested as (
    select distinct upper(replace(tag, '#', '')) as player_tag
    from unnest(coalesce(p_player_tags, array[]::text[])) as tag
    where tag is not null and tag <> ''
  ), boundary as (
    select case
      when p_days is null then null::timestamptz
      when p_days = 0 then date_trunc('day', now() at time zone 'Europe/Rome') at time zone 'Europe/Rome'
      else now() - make_interval(days => p_days)
    end as target
  )
  select requested.player_tag, latest.player_name, latest.club_name,
    latest.coefficient_value, latest.coefficient,
    coalesce(before_boundary.coefficient_value, first_today.coefficient_value),
    case
      when p_days is null then latest.coefficient_value
      when coalesce(before_boundary.coefficient_value, first_today.coefficient_value) is null then null
      else latest.coefficient_value - coalesce(before_boundary.coefficient_value, first_today.coefficient_value)
    end,
    latest.recorded_at
  from requested cross join boundary
  join lateral (
    select h.player_name, h.club_name, h.coefficient_value, h.coefficient,
      h.formula_version, h.recorded_at
    from public.coefficient_history h
    where h.player_tag = requested.player_tag
    order by h.recorded_at desc limit 1
  ) latest on true
  left join lateral (
    select h.coefficient_value
    from public.coefficient_history h
    where h.player_tag = requested.player_tag
      and h.formula_version = latest.formula_version
      and boundary.target is not null and h.recorded_at <= boundary.target
    order by h.recorded_at desc limit 1
  ) before_boundary on true
  left join lateral (
    select h.coefficient_value
    from public.coefficient_history h
    where h.player_tag = requested.player_tag
      and h.formula_version = latest.formula_version
      and p_days = 0 and boundary.target is not null
      and h.recorded_at >= boundary.target
    order by h.recorded_at asc limit 1
  ) first_today on before_boundary.coefficient_value is null;
$$;
