-- A save retry can reuse an older object path. Without a durable upload lease,
-- deleting unreferenced files for active accounts could race with that retry.
-- Clean missing-account leftovers automatically; account deletion already removes
-- all of that owner's files. Keep active-account files until explicit removal.
create or replace function public.orphaned_wardrobe_images() returns table(name text)
language sql security invoker set search_path='' as $$
 select o.name from storage.objects o where o.bucket_id='wardrobe-items'
 and o.created_at < now()-interval '24 hours'
 and not exists(select 1 from public.wardrobe_items w where w.image_path=o.name)
 and not exists(select 1 from auth.users u where u.id::text=split_part(o.name,'/',1))
 order by o.created_at limit 100
$$;
