CREATE OR REPLACE FUNCTION public.coefficient_progression_rows_range(p_player_tags text[], p_start timestamp with time zone, p_end timestamp with time zone)
 RETURNS TABLE(player_tag text, player_name text, club_name text, coefficient_value integer, coefficient numeric, baseline_value integer, progression_value integer, positive_trophies integer, battle_count integer, play_seconds integer, recorded_at timestamp with time zone)
 LANGUAGE sql
 STABLE
 SET search_path TO ''
AS $function$
with requested as (
 select distinct upper(replace(tag,'#','')) player_tag from unnest(coalesce(p_player_tags,array[]::text[])) tag where tag is not null and tag<>''
), boundary as (select p_start target
), latest as (
 select r.player_tag,h.player_name,h.club_name,h.coefficient_value,h.coefficient,h.recorded_at
 from requested r join lateral (select h.player_name,h.club_name,h.coefficient_value,h.coefficient,h.recorded_at from public.coefficient_history h where h.player_tag=r.player_tag order by h.recorded_at desc limit 1) h on true
), battles as (
 select b.player_tag,b.player_name,b.battle_time,b.brawler_trophies_before::int t0,b.trophy_change::int d,b.brawler_trophies_before::int ref
 from public.observed_trophy_battles b cross join boundary join requested r on r.player_tag=b.player_tag
 where p_start is not null and p_end > p_start and b.trophy_change is not null and b.brawler_trophies_before is not null and b.battle_time>=boundary.target and b.battle_time < p_end and coalesce(b.bonus_type,'') not like 'excluded%'
), gaps as (
 select b.*, extract(epoch from (battle_time-lag(battle_time) over(partition by player_tag order by battle_time)))::int gap_seconds from battles b
), sessioned as (
 select g.*, sum(case when gap_seconds is null or gap_seconds>600 then 1 else 0 end) over(partition by player_tag order by battle_time) session_id from gaps g
), session_bounds as (
 select player_tag,session_id,min(battle_time) started_at,max(battle_time) ended_at,count(*) battle_count from sessioned group by player_tag,session_id
), playtime as (
 select player_tag,coalesce(sum(case when battle_count>1 then extract(epoch from (ended_at-started_at)) else 0 end),0)::int play_seconds from session_bounds group by player_tag
), weighted as (
 select player_tag,
 round(sum(case when d<=0 then 0 else
 greatest(0,least(ref+d,50)-greatest(ref,0))*1.0000+greatest(0,least(ref+d,100)-greatest(ref,50))*1.0250+
 greatest(0,least(ref+d,200)-greatest(ref,100))*1.0285+greatest(0,least(ref+d,300)-greatest(ref,200))*1.0320+
 greatest(0,least(ref+d,500)-greatest(ref,300))*1.0500+greatest(0,least(ref+d,600)-greatest(ref,500))*1.0590+
 greatest(0,least(ref+d,800)-greatest(ref,600))*1.0680+greatest(0,least(ref+d,1000)-greatest(ref,800))*1.0970+
 greatest(0,least(ref+d,1100)-greatest(ref,1000))*1.1180+greatest(0,least(ref+d,1200)-greatest(ref,1100))*1.1360+
 greatest(0,least(ref+d,1300)-greatest(ref,1200))*1.1650+greatest(0,least(ref+d,1500)-greatest(ref,1300))*1.1900+
 greatest(0,least(ref+d,1800)-greatest(ref,1500))*1.2080+greatest(0,least(ref+d,2000)-greatest(ref,1800))*1.2370+
 greatest(0,least(ref+d,2200)-greatest(ref,2000))*1.5500+greatest(0,least(ref+d,2300)-greatest(ref,2200))*1.5900+
 greatest(0,least(ref+d,2400)-greatest(ref,2300))*1.6300+greatest(0,least(ref+d,2500)-greatest(ref,2400))*1.6700+
 greatest(0,least(ref+d,2600)-greatest(ref,2500))*1.7285+greatest(0,least(ref+d,2700)-greatest(ref,2600))*1.8000+
 greatest(0,least(ref+d,2800)-greatest(ref,2700))*1.8800+greatest(0,least(ref+d,3000)-greatest(ref,2800))*2.0000+
 greatest(0,ref+d-greatest(ref,3000))*1.0 end))::int progression_value,
 coalesce(sum(greatest(d,0)),0)::int positive_trophies,count(*)::int battle_count
 from battles group by player_tag
)
select l.player_tag,l.player_name,l.club_name,l.coefficient_value,l.coefficient,null::int baseline_value,
coalesce(w.progression_value,0),
coalesce(w.positive_trophies,0),
coalesce(w.battle_count,0),
coalesce(pt.play_seconds,0),l.recorded_at
from latest l left join weighted w using(player_tag) left join playtime pt using(player_tag);
$function$;
