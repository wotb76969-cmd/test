-- Isolation self-check for Chelsy AI tables (run in the SQL editor; read-only).
-- 1) RLS must be enabled on every Chelsy table:
select tablename, rowsecurity from pg_tables
 where schemaname = 'public' and tablename like 'chelsy_ai_%' order by 1;
-- 2) Every policy must restrict rows to auth.uid():
select tablename, policyname, cmd, qual, with_check from pg_policies
 where schemaname = 'public' and tablename like 'chelsy_ai_%' order by 1, 2;
-- 3) The anon role must have no table privileges:
select table_name, privilege_type from information_schema.role_table_grants
 where grantee = 'anon' and table_name like 'chelsy_ai_%';
