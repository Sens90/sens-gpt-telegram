-- Freeze the 23:59 reports across a Render restart so a failed Telegram send
-- can resume on the next watchdog pass without rebuilding "oggi" after midnight.
alter table public.community_settings
    add column if not exists auto_ranking_pending jsonb;
