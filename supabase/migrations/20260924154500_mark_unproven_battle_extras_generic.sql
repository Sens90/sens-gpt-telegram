-- Supercell battle logs expose the measured trophy extra but do not expose a
-- reliable Win Streak or Underdog cause field. Preserve the numeric evidence
-- and replace only unsupported cause labels.
update public.observed_trophy_battles
set bonus_type = 'bonus_observed'
where coalesce(observed_extra, 0) > 0
  and bonus_type in (
    'win_streak_observed',
    'underdog_observed',
    'win_streak_plus_underdog_observed'
  );
