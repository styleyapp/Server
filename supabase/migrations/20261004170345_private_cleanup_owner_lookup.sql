-- Auth tables are not readable by service_role. Keep the minimal existence check
-- in an unexposed schema instead of granting broad Auth-table SELECT privileges.
create function private.cleanup_owner_exists(p_owner text) returns boolean
language sql security definer set search_path='' as $$
 select exists(select 1 from auth.users u where u.id::text=p_owner)
$$;
revoke all on function private.cleanup_owner_exists(text) from public,anon,authenticated;
grant usage on schema private to service_role;
grant execute on function private.cleanup_owner_exists(text) to service_role;
create or replace function public.orphaned_wardrobe_images() returns table(name text)
language sql security invoker set search_path='' as $$
 select o.name from storage.objects o where o.bucket_id='wardrobe-items'
 and o.created_at < now()-interval '24 hours'
 and not exists(select 1 from public.wardrobe_items w where w.image_path=o.name)
 and not private.cleanup_owner_exists(split_part(o.name,'/',1))
 order by o.created_at limit 100
$$;
