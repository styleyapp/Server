-- Keep the narrowly scoped RLS helper outside the public Data API.
-- Existing policy references follow the function OID when its schema changes.
alter function public.account_is_active(uuid) set schema private;
grant usage on schema private to authenticated;
create policy scan_requests_server_only on public.scan_requests
for all to anon,authenticated using (false) with check (false);
create policy account_deletions_server_only on public.account_deletions
for all to anon,authenticated using (false) with check (false);
