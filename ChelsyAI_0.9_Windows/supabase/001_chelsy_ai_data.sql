-- Chelsy AI 0.8: additive schema. Does not modify auth.users or existing website tables.
-- Review in a staging Supabase project before executing in production.
create table if not exists public.chelsy_ai_messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  legacy_id text,
  role text not null check (role in ('user','assistant')),
  content text not null,
  created_at timestamptz not null default now(),
  unique (user_id, legacy_id)
);
create index if not exists chelsy_ai_messages_user_date on public.chelsy_ai_messages(user_id, created_at desc);

create table if not exists public.chelsy_ai_memories (
  user_id uuid not null references auth.users(id) on delete cascade,
  memory_key text not null,
  value text not null,
  updated_at timestamptz not null default now(),
  primary key(user_id,memory_key)
);

alter table public.chelsy_ai_messages enable row level security;
alter table public.chelsy_ai_memories enable row level security;
revoke all on public.chelsy_ai_messages from anon, authenticated;
revoke all on public.chelsy_ai_memories from anon, authenticated;
grant select, insert, update, delete on public.chelsy_ai_messages to authenticated;
grant select, insert, update, delete on public.chelsy_ai_memories to authenticated;

create policy chelsy_messages_select_own on public.chelsy_ai_messages for select to authenticated
  using (user_id = (select auth.uid()));
create policy chelsy_messages_insert_own on public.chelsy_ai_messages for insert to authenticated
  with check (user_id = (select auth.uid()));
create policy chelsy_messages_update_own on public.chelsy_ai_messages for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy chelsy_messages_delete_own on public.chelsy_ai_messages for delete to authenticated
  using (user_id = (select auth.uid()));

create policy chelsy_memories_select_own on public.chelsy_ai_memories for select to authenticated
  using (user_id = (select auth.uid()));
create policy chelsy_memories_insert_own on public.chelsy_ai_memories for insert to authenticated
  with check (user_id = (select auth.uid()));
create policy chelsy_memories_update_own on public.chelsy_ai_memories for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy chelsy_memories_delete_own on public.chelsy_ai_memories for delete to authenticated
  using (user_id = (select auth.uid()));
