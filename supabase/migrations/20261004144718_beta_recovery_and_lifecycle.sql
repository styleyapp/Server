-- Durable intake leases and a deletion outbox. Client writes are never allowed.
create table public.scan_requests (
  user_id uuid not null references auth.users(id) on delete cascade,
  request_id uuid not null, content_hash text not null check(length(content_hash)=64),
  state text not null default 'pending' check(state in ('pending','ready','failed')),
  lease_token uuid not null default gen_random_uuid(),
  lease_until timestamptz not null default now()+interval '8 minutes',
  attempts integer not null default 1 check(attempts between 1 and 3),
  created_at timestamptz not null default now(), expires_at timestamptz not null default now()+interval '24 hours',
  primary key(user_id,request_id)
);
create index scan_requests_recent on public.scan_requests(user_id,created_at desc);
create index wardrobe_items_pagination on public.wardrobe_items(user_id,created_at desc,id desc);
create table public.account_deletions (
  user_id uuid primary key, requested_at timestamptz not null default now(),
  completed_at timestamptz, last_attempt_at timestamptz, attempts integer not null default 0
);
alter table public.scan_requests enable row level security;
alter table public.account_deletions enable row level security;
revoke all on public.scan_requests,public.account_deletions from anon,authenticated;
grant all on public.scan_requests,public.account_deletions to service_role;
-- Recovery results stay private and are only read through the authenticated Server.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('scan-results','scan-results',false,24000000,array['application/json']);
create function public.claim_scan(p_user uuid,p_request uuid,p_hash text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare target public.scan_requests; begin
  perform pg_advisory_xact_lock(hashtextextended(p_user::text,0));
  if exists(select 1 from public.account_deletions where user_id=p_user) then
    return jsonb_build_object('status','unauthorized'); end if;
  select * into target from public.scan_requests where user_id=p_user and request_id=p_request for update;
  if found then
    if target.content_hash<>p_hash then return jsonb_build_object('status','conflict'); end if;
    if target.expires_at<=now() then return jsonb_build_object('status','expired'); end if;
    if target.state='ready' then return jsonb_build_object('status','ready'); end if;
    if target.state='pending' and target.lease_until>now() then return jsonb_build_object('status','generation_in_progress'); end if;
    if target.attempts>=3 then return jsonb_build_object('status','rate_limited'); end if;
  elsif (select count(*) from public.scan_requests where user_id=p_user and created_at>now()-interval '1 hour')>=6
    or (select count(*) from public.scan_requests where user_id=p_user and created_at>now()-interval '24 hours')>=24 then
    return jsonb_build_object('status','rate_limited');
  end if;
  if exists(select 1 from public.scan_requests where user_id=p_user and state='pending'
    and lease_until>now() and request_id<>p_request) then return jsonb_build_object('status','generation_in_progress'); end if;
  if target.request_id is null then
    insert into public.scan_requests(user_id,request_id,content_hash) values(p_user,p_request,p_hash) returning * into target;
  else
    update public.scan_requests set state='pending',lease_token=gen_random_uuid(),lease_until=now()+interval '8 minutes',attempts=attempts+1
      where user_id=p_user and request_id=p_request returning * into target;
  end if;
  return jsonb_build_object('status','claimed','lease_token',target.lease_token);
end $$;
revoke all on function public.claim_scan(uuid,uuid,text) from public,anon,authenticated;
grant execute on function public.claim_scan(uuid,uuid,text) to service_role;
-- Block direct client garment mutations after a deletion request has been accepted.
create function public.account_is_active(p_user uuid) returns boolean language sql
security definer set search_path='' as $$
 select p_user=(select auth.uid()) and not exists(select 1 from public.account_deletions where user_id=p_user)
$$;
revoke all on function public.account_is_active(uuid) from public,anon;
grant execute on function public.account_is_active(uuid) to authenticated;
create policy wardrobe_active_account on public.wardrobe_items as restrictive for all to authenticated
using (public.account_is_active(user_id)) with check(public.account_is_active(user_id));
create schema if not exists private;
revoke all on schema private from public,anon,authenticated;
create function private.reject_deleting_account() returns trigger language plpgsql security definer set search_path='' as $$
begin
  if exists(select 1 from public.account_deletions where user_id=new.user_id) then
    raise exception 'Account unavailable' using errcode='23514';
  end if;
  return new;
end $$;
revoke all on function private.reject_deleting_account() from public,anon,authenticated;
create trigger wardrobe_deletion_guard before insert or update on public.wardrobe_items
for each row execute function private.reject_deleting_account();
create trigger outfit_deletion_guard before insert or update on public.outfits
for each row execute function private.reject_deleting_account();
create trigger preferences_deletion_guard before insert or update on public.user_preferences
for each row execute function private.reject_deleting_account();
create policy wardrobe_storage_active_account on storage.objects as restrictive for all to authenticated
using (bucket_id<>'wardrobe-items' or public.account_is_active((select auth.uid())))
with check(bucket_id<>'wardrobe-items' or public.account_is_active((select auth.uid())));

-- Delete storage objects through the Storage API, never directly from storage.objects.
create function public.orphaned_wardrobe_images() returns table(name text)
language sql security invoker set search_path='' as $$
 select o.name from storage.objects o where o.bucket_id='wardrobe-items'
 and o.created_at < now()-interval '24 hours'
 and not exists(select 1 from public.wardrobe_items w where w.image_path=o.name)
 order by o.created_at limit 100
$$;
revoke all on function public.orphaned_wardrobe_images() from public,anon,authenticated;
grant execute on function public.orphaned_wardrobe_images() to service_role;
