-- Additive indexes for Chelsy 0.9. Apply AFTER 001_chelsy_ai_data.sql.
-- No existing website tables, auth.users, or passwords are modified.
create index if not exists chelsy_ai_messages_uid_created
  on public.chelsy_ai_messages (user_id, created_at asc, id asc);
create index if not exists chelsy_ai_memories_uid_updated
  on public.chelsy_ai_memories (user_id, updated_at desc);
-- IMPORTANT: both tables' RLS and per-user policies from 001 must remain enabled.
