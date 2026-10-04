-- Additive fields preserve existing items and owner RLS policies.
alter table public.wardrobe_items
  add column hebrew jsonb check (hebrew is null or jsonb_typeof(hebrew) = 'object'),
  add column length text not null default '' check (length in ('', 'short', 'regular', 'long')),
  add column is_favorite boolean not null default false;
