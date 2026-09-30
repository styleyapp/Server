create table if not exists public.wardrobe_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (char_length(name) between 1 and 120),
  brand text not null default '' check (char_length(brand) <= 120),
  category text not null default '' check (char_length(category) <= 80),
  type text not null default '' check (char_length(type) <= 80),
  color text not null default '' check (char_length(color) <= 80),
  season text not null default '' check (char_length(season) <= 80),
  tags text[] not null default '{}',
  source text not null check (source in ('photo', 'video')),
  source_candidate_id uuid not null,
  image_path text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index wardrobe_items_user_created_idx
  on public.wardrobe_items (user_id, created_at desc);
create unique index wardrobe_items_user_candidate_idx
  on public.wardrobe_items (user_id, source_candidate_id);

alter table public.wardrobe_items enable row level security;

create policy "Owners read wardrobe items"
  on public.wardrobe_items for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Owners create wardrobe items"
  on public.wardrobe_items for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Owners update wardrobe items"
  on public.wardrobe_items for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy "Owners delete wardrobe items"
  on public.wardrobe_items for delete to authenticated
  using ((select auth.uid()) = user_id);

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('wardrobe-items', 'wardrobe-items', false, 3145728, array['image/png', 'image/jpeg'])
on conflict (id) do nothing;

create policy "Owners read wardrobe images"
  on storage.objects for select to authenticated
  using (bucket_id = 'wardrobe-items' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "Owners upload wardrobe images"
  on storage.objects for insert to authenticated
  with check (bucket_id = 'wardrobe-items' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "Owners delete wardrobe images"
  on storage.objects for delete to authenticated
  using (bucket_id = 'wardrobe-items' and (storage.foldername(name))[1] = (select auth.uid())::text);
