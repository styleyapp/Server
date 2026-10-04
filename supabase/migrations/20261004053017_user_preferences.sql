-- Writes go through the Server's revision-checked API, never direct client updates.
create table public.user_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  preferences jsonb not null,
  revision bigint not null check (revision >= 1),
  updated_at timestamptz not null default now(),
  constraint user_preferences_shape check ((
    jsonb_typeof(preferences) = 'object'
    and preferences ?& array['closet_categories', 'age', 'top_fit', 'pants_fit', 'locale']
    and (preferences - array['closet_categories', 'age', 'top_fit', 'pants_fit', 'locale']) = '{}'::jsonb
    and jsonb_typeof(preferences->'closet_categories') = 'array'
    and preferences->'closet_categories' in ('[]'::jsonb, '["men"]'::jsonb, '["women"]'::jsonb, '["men","women"]'::jsonb)
    and preferences->>'top_fit' in ('slim', 'regular', 'relaxed')
    and preferences->>'pants_fit' in ('skinny', 'straight', 'baggy')
    and preferences->>'locale' in ('en', 'he')
    and (preferences->'age' = 'null'::jsonb or (
      jsonb_typeof(preferences->'age') = 'number'
      and (preferences->>'age') ~ '^[0-9]+$'
      and (preferences->>'age')::numeric between 13 and 65
    ))
  ) is true)
);

alter table public.user_preferences enable row level security;
revoke all on public.user_preferences from public, anon, authenticated;
grant select on public.user_preferences to authenticated;
grant select, insert, update, delete on public.user_preferences to service_role;
create policy "Owners read preferences" on public.user_preferences
  for select to authenticated using ((select auth.uid()) = user_id);

create function public.touch_user_preferences() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
  new.updated_at := now();
  return new;
end;
$$;
revoke all on function public.touch_user_preferences() from public, anon, authenticated;
create trigger user_preferences_updated before update on public.user_preferences
  for each row execute function public.touch_user_preferences();
