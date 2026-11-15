-- Chelsy AI 0.9.3 — conversations, soft delete, updated_at.  ADDITIVE ONLY.
-- Apply AFTER 001 and 002. Does not touch auth.users, website tables, existing rows or passwords.
-- Run in the Supabase SQL editor (staging first). Re-running is safe (idempotent).
-- Rollback: 003_rollback.sql (read the warning inside first).
begin;

create table if not exists public.chelsy_ai_conversations (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null default 'Новый чат',
  favorite boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);
do $$ begin
  alter table public.chelsy_ai_conversations
    add constraint chelsy_ai_conversations_title_len check (char_length(title) between 1 and 200);
exception when duplicate_object then null; end $$;
create index if not exists chelsy_ai_conversations_user_updated
  on public.chelsy_ai_conversations (user_id, updated_at desc);

alter table public.chelsy_ai_conversations enable row level security;
revoke all on public.chelsy_ai_conversations from anon, authenticated;
grant select, insert, update, delete on public.chelsy_ai_conversations to authenticated;

drop policy if exists chelsy_conversations_select_own on public.chelsy_ai_conversations;
drop policy if exists chelsy_conversations_insert_own on public.chelsy_ai_conversations;
drop policy if exists chelsy_conversations_update_own on public.chelsy_ai_conversations;
drop policy if exists chelsy_conversations_delete_own on public.chelsy_ai_conversations;
create policy chelsy_conversations_select_own on public.chelsy_ai_conversations for select to authenticated
  using (user_id = (select auth.uid()));
create policy chelsy_conversations_insert_own on public.chelsy_ai_conversations for insert to authenticated
  with check (user_id = (select auth.uid()));
create policy chelsy_conversations_update_own on public.chelsy_ai_conversations for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy chelsy_conversations_delete_own on public.chelsy_ai_conversations for delete to authenticated
  using (user_id = (select auth.uid()));

-- Messages: link to a conversation, soft delete, change tracking. Existing rows keep NULLs.
alter table public.chelsy_ai_messages
  add column if not exists conversation_id uuid references public.chelsy_ai_conversations(id) on delete set null,
  add column if not exists deleted_at timestamptz,
  add column if not exists updated_at timestamptz not null default now();
create index if not exists chelsy_ai_messages_user_conv_created
  on public.chelsy_ai_messages (user_id, conversation_id, created_at);
-- Size guard for NEW rows only (NOT VALID keeps historical rows untouched).
do $$ begin
  alter table public.chelsy_ai_messages
    add constraint chelsy_ai_messages_content_len check (char_length(content) <= 100000) not valid;
exception when duplicate_object then null; end $$;

-- A message may only reference a conversation of the SAME user.
-- SECURITY INVOKER: the lookup itself is subject to RLS, so other users' rows are invisible.
create or replace function public.chelsy_ai_check_message_conversation()
returns trigger language plpgsql security invoker set search_path = public as $$
begin
  if new.conversation_id is not null and not exists (
      select 1 from public.chelsy_ai_conversations c
      where c.id = new.conversation_id and c.user_id = new.user_id) then
    raise exception 'conversation % does not belong to this user', new.conversation_id
      using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists chelsy_ai_messages_conversation_owner on public.chelsy_ai_messages;
create trigger chelsy_ai_messages_conversation_owner
  before insert or update of conversation_id, user_id on public.chelsy_ai_messages
  for each row execute function public.chelsy_ai_check_message_conversation();

create or replace function public.chelsy_ai_touch_updated_at()
returns trigger language plpgsql security invoker set search_path = public as $$
begin
  new.updated_at := now();
  return new;
end $$;
drop trigger if exists chelsy_ai_messages_touch on public.chelsy_ai_messages;
create trigger chelsy_ai_messages_touch before update on public.chelsy_ai_messages
  for each row execute function public.chelsy_ai_touch_updated_at();
-- Conversations keep the client-supplied updated_at (used for last-writer-wins);
-- it may not move into the future by more than 5 minutes.
create or replace function public.chelsy_ai_clamp_conversation_time()
returns trigger language plpgsql security invoker set search_path = public as $$
begin
  if new.updated_at is null or new.updated_at > now() + interval '5 minutes' then
    new.updated_at := now();
  end if;
  return new;
end $$;
drop trigger if exists chelsy_ai_conversations_clamp on public.chelsy_ai_conversations;
create trigger chelsy_ai_conversations_clamp before insert or update on public.chelsy_ai_conversations
  for each row execute function public.chelsy_ai_clamp_conversation_time();

revoke all on function public.chelsy_ai_check_message_conversation() from public, anon;
revoke all on function public.chelsy_ai_touch_updated_at() from public, anon;
revoke all on function public.chelsy_ai_clamp_conversation_time() from public, anon;

commit;

-- Verification (run separately, must return rowsecurity = true for all three):
-- select tablename, rowsecurity from pg_tables
--  where schemaname = 'public' and tablename like 'chelsy_ai_%';
