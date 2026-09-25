create table if not exists public.scheduled_dashboard_delivery (
  chat_id bigint not null,
  slot text not null,
  payload jsonb,
  sent_at timestamptz,
  primary key (chat_id, slot)
);

alter table public.scheduled_dashboard_delivery enable row level security;
revoke all on public.scheduled_dashboard_delivery from anon, authenticated;
