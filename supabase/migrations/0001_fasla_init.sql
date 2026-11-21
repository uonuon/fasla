-- Fasla: tables, row-level security, and the private file bucket.
-- Run once in Supabase: Dashboard -> SQL Editor -> paste -> Run.

create extension if not exists pgcrypto;

-- Brands: one user can run several brands
create table if not exists public.brands (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null check (char_length(name) between 1 and 80),
  created_at  timestamptz not null default now()
);
create index if not exists brands_user_idx on public.brands(user_id);

-- Plans: one uploaded workbook and its result
create table if not exists public.plans (
  id           uuid primary key default gen_random_uuid(),
  brand_id     uuid not null references public.brands(id) on delete cascade,
  user_id      uuid not null references auth.users(id) on delete cascade,
  status       varchar(16) not null default 'draft'
               check (status in ('draft','queued','running','done','failed')),
  input_name   text,
  input_path   text,
  report_path  text,
  settings     jsonb,
  checks       jsonb,
  summary      jsonb,
  view         jsonb,
  error        text,
  created_at   timestamptz not null default now(),
  finished_at  timestamptz
);
create index if not exists plans_brand_idx on public.plans(brand_id);
create index if not exists plans_user_idx on public.plans(user_id);

-- A plan must belong to a brand of the same user
create or replace function public.plan_brand_owner_check() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if not exists (select 1 from public.brands b where b.id = new.brand_id and b.user_id = new.user_id) then
    raise exception 'brand does not belong to this user';
  end if;
  return new;
end $$;
drop trigger if exists plans_owner_check on public.plans;
create trigger plans_owner_check before insert or update of brand_id, user_id on public.plans
  for each row execute function public.plan_brand_owner_check();

-- Row-level security: every user sees only their own rows.
-- (The Fasla server talks to the database directly and also filters by user; these rules protect the
-- data if anyone ever queries the tables with a user's token.)
alter table public.brands enable row level security;
alter table public.plans  enable row level security;

drop policy if exists "brands: owner can read"   on public.brands;
drop policy if exists "brands: owner can insert" on public.brands;
drop policy if exists "brands: owner can update" on public.brands;
drop policy if exists "brands: owner can delete" on public.brands;
create policy "brands: owner can read"   on public.brands for select to authenticated using (user_id = auth.uid());
create policy "brands: owner can insert" on public.brands for insert to authenticated with check (user_id = auth.uid());
create policy "brands: owner can update" on public.brands for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "brands: owner can delete" on public.brands for delete to authenticated using (user_id = auth.uid());

drop policy if exists "plans: owner can read"   on public.plans;
drop policy if exists "plans: owner can delete" on public.plans;
create policy "plans: owner can read"   on public.plans for select to authenticated using (user_id = auth.uid());
create policy "plans: owner can delete" on public.plans for delete to authenticated using (user_id = auth.uid());
-- inserts/updates of plans only come from the server (it runs the planner)

revoke all on public.brands, public.plans from anon;

-- Private bucket for uploaded workbooks and reports (10 MB max per file)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('fasla-files', 'fasla-files', false, 10485760,
        array['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'])
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

-- Files live under <user id>/<brand id>/<plan id>/...; a signed-in user may read only their own folder
drop policy if exists "fasla files: owner can read" on storage.objects;
create policy "fasla files: owner can read" on storage.objects for select to authenticated
  using (bucket_id = 'fasla-files' and (storage.foldername(name))[1] = auth.uid()::text);
