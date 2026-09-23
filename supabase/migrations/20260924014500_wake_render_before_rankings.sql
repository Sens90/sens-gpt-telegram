-- Render's free web instance sleeps when the Telegram webhook receives no
-- traffic. Wake it shortly before each Rome-local automatic ranking slot so
-- the in-process APScheduler is running at 06:00, 12:00, 18:00 and 23:59.
-- The minute check is evaluated in Europe/Rome, so daylight-saving changes do
-- not shift the requested delivery times.

create schema if not exists extensions;
create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron;

select cron.schedule(
    'wake-render-before-automatic-rankings',
    '* * * * *',
    $job$
    select net.http_get(
        url := 'https://sens-gpt-telegram.onrender.com/',
        headers := '{"User-Agent":"SensGPT-Scheduler-Wakeup/1.0"}'::jsonb,
        timeout_milliseconds := 10000
    )
    where to_char(now() at time zone 'Europe/Rome', 'HH24:MI')
        in ('05:55', '11:55', '17:55', '23:54');
    $job$
);
