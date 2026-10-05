-- Free Supabase projects pause after 7 days without database activity. A daily GitHub Actions job
-- (.github/workflows/supabase-keepalive.yml) calls this with the public key so the project stays awake.
-- Returns nothing private.
create function public.shorts_ping()
returns timestamptz
language sql
stable
set search_path = ''
as $$ select now() $$;

revoke all on function public.shorts_ping() from public;
grant execute on function public.shorts_ping() to anon, authenticated;
