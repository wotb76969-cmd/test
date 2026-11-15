-- ROLLBACK of 003 (Chelsy AI 0.9.3). WARNING: removes conversation titles/favourites and the
-- conversation links / soft-delete marks of messages. Message texts and memories are KEPT.
-- Export the tables first (Table editor -> Export CSV) if you may need them again.
-- 0.9.2 clients keep working without 003; the 0.9.3 host automatically falls back to legacy sync.
begin;
drop trigger if exists chelsy_ai_messages_conversation_owner on public.chelsy_ai_messages;
drop trigger if exists chelsy_ai_messages_touch on public.chelsy_ai_messages;
drop trigger if exists chelsy_ai_conversations_clamp on public.chelsy_ai_conversations;
drop function if exists public.chelsy_ai_check_message_conversation();
drop function if exists public.chelsy_ai_touch_updated_at();
drop function if exists public.chelsy_ai_clamp_conversation_time();
alter table public.chelsy_ai_messages drop constraint if exists chelsy_ai_messages_content_len;
drop index if exists public.chelsy_ai_messages_user_conv_created;
alter table public.chelsy_ai_messages
  drop column if exists conversation_id,
  drop column if exists deleted_at,
  drop column if exists updated_at;
drop table if exists public.chelsy_ai_conversations;
commit;
