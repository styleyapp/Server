-- Keep the event trigger active for database DDL without exposing its
-- SECURITY DEFINER function through the Data API.
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;
