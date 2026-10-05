-- Shorts Everywhere app accounts (Supabase project shorts-everywhere, xzbsyxvovxryisqppbbr).
-- Logins themselves are Supabase Auth (auth.users, email + password). This table records who has signed in to the
-- app and when, one row per user. Clients can read only their own row and can never write it directly: the only
-- write path is shorts_record_login(), which the dashboard calls after every successful sign-in.

create table public.shorts_users (
  id             uuid primary key references auth.users (id) on delete cascade,
  email          text        not null,
  first_login_at timestamptz not null default now(),
  last_login_at  timestamptz not null default now(),
  login_count    integer     not null default 1
);

alter table public.shorts_users enable row level security;

create policy shorts_users_select_self on public.shorts_users
  for select to authenticated using (id = (select auth.uid()));

-- New projects grant table privileges to anon/authenticated by default; RLS already blocks writes (no insert/update
-- policy), but don't rely on that alone.
revoke insert, update, delete, truncate on public.shorts_users from anon, authenticated;
revoke all on public.shorts_users from anon;

create function public.shorts_record_login()
returns public.shorts_users
language plpgsql
security definer
set search_path = ''
as $$
declare
  me     uuid := auth.uid();
  result public.shorts_users;
begin
  if me is null then
    raise exception 'Sign in first.' using errcode = '28000';
  end if;
  insert into public.shorts_users (id, email)
  values (me, coalesce(auth.jwt() ->> 'email', ''))
  on conflict (id) do update
    set last_login_at = now(),
        login_count   = public.shorts_users.login_count + 1,
        email         = excluded.email
  returning * into result;
  return result;
end;
$$;

revoke all on function public.shorts_record_login() from public, anon;
grant execute on function public.shorts_record_login() to authenticated;
