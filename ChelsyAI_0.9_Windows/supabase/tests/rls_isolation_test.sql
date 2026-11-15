-- Real-database checks of RLS isolation and of the 003 trigger. Fails loudly on any violation.
\set ON_ERROR_STOP 1
insert into auth.users(id,email) values
  ('11111111-1111-4111-8111-111111111111','alice@example.org'),
  ('22222222-2222-4222-8222-222222222222','bob@example.org')
on conflict do nothing;

-- Alice writes her data
set role authenticated;
select set_config('request.jwt.claim.sub','11111111-1111-4111-8111-111111111111', false);
insert into public.chelsy_ai_conversations(id,user_id,title) values
  ('aaaaaaaa-0000-4000-8000-000000000001','11111111-1111-4111-8111-111111111111','Секрет Алисы');
insert into public.chelsy_ai_messages(user_id,legacy_id,role,content,conversation_id) values
  ('11111111-1111-4111-8111-111111111111','msg:a1','user','Привет','aaaaaaaa-0000-4000-8000-000000000001');
insert into public.chelsy_ai_memories(user_id,memory_key,value) values
  ('11111111-1111-4111-8111-111111111111','цвет','фиолетовый');
-- idempotent upsert used by the host (merge duplicates)
insert into public.chelsy_ai_messages(user_id,legacy_id,role,content,conversation_id,deleted_at) values
  ('11111111-1111-4111-8111-111111111111','msg:a1','user','Привет','aaaaaaaa-0000-4000-8000-000000000001', now())
on conflict (user_id,legacy_id) do update set deleted_at = excluded.deleted_at;
do $$ begin
  if (select count(*) from public.chelsy_ai_messages) <> 1 then raise exception 'duplicate message created'; end if;
  if (select deleted_at from public.chelsy_ai_messages) is null then raise exception 'soft delete not stored'; end if;
end $$;

-- Bob must not see or touch Alice's rows
select set_config('request.jwt.claim.sub','22222222-2222-4222-8222-222222222222', false);
do $$ begin
  if (select count(*) from public.chelsy_ai_conversations) <> 0 then raise exception 'RLS leak: conversations'; end if;
  if (select count(*) from public.chelsy_ai_messages) <> 0 then raise exception 'RLS leak: messages'; end if;
  if (select count(*) from public.chelsy_ai_memories) <> 0 then raise exception 'RLS leak: memories'; end if;
end $$;
update public.chelsy_ai_conversations set title='взлом' where id='aaaaaaaa-0000-4000-8000-000000000001';
delete from public.chelsy_ai_messages;
do $$ declare failed boolean := false; begin
  begin
    insert into public.chelsy_ai_messages(user_id,legacy_id,role,content)
      values ('11111111-1111-4111-8111-111111111111','msg:forged','user','подделка');
  exception when insufficient_privilege then failed := true; end;
  if not failed then raise exception 'RLS allowed writing as another user'; end if;
  failed := false;
  begin
    insert into public.chelsy_ai_messages(user_id,legacy_id,role,content,conversation_id)
      values ('22222222-2222-4222-8222-222222222222','msg:b1','user','в чужой чат','aaaaaaaa-0000-4000-8000-000000000001');
  exception when insufficient_privilege then failed := true; end;
  if not failed then raise exception 'message linked to another user''s conversation'; end if;
end $$;

-- anon has no access at all
reset role;
set role anon;
do $$ declare failed boolean := false; begin
  begin perform 1 from public.chelsy_ai_messages; exception when insufficient_privilege then failed := true; end;
  if not failed then raise exception 'anon can read messages'; end if;
end $$;
reset role;

-- Alice's data is intact after Bob's attempts
do $$ begin
  if (select title from public.chelsy_ai_conversations where id='aaaaaaaa-0000-4000-8000-000000000001') <> 'Секрет Алисы'
    then raise exception 'Bob modified Alice''s conversation'; end if;
  if (select count(*) from public.chelsy_ai_messages) <> 1 then raise exception 'Bob deleted Alice''s messages'; end if;
  if exists (select 1 from pg_tables where schemaname='public' and tablename like 'chelsy_ai_%' and not rowsecurity)
    then raise exception 'RLS disabled on a Chelsy table'; end if;
end $$;
select 'RLS isolation test: PASSED' as result;
