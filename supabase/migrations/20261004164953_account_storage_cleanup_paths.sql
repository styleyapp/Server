-- Enumerate bounded owned paths without assuming the Storage folder layout.
-- Objects themselves are removed through the Storage API by the maintenance worker.
create function public.account_storage_paths(p_user uuid,p_bucket text)
returns table(name text) language sql security invoker set search_path='' as $$
 select o.name from storage.objects o
 where o.bucket_id=p_bucket and p_bucket in ('wardrobe-items','scan-results')
 and starts_with(o.name,p_user::text||'/')
 order by o.name limit 100
$$;
revoke all on function public.account_storage_paths(uuid,text) from public,anon,authenticated;
grant execute on function public.account_storage_paths(uuid,text) to service_role;
