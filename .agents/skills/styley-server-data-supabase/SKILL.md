---
name: styley-server-data-supabase
description: Design, migrate, query, optimize, or review Styley's Supabase and Postgres data layer, RLS, storage, realtime, and schema. Use for any database or Supabase task.
---

# Styley Server Data Supabase

Load the available `supabase` and `supabase-postgres-best-practices` skills before implementation and verify current behavior against official documentation and the checked-in CLI and client versions.

- Make durable schema changes through ordered, reviewable migrations. Never edit an applied migration to change production history.
- Enable RLS on every table exposed through the Data API and test policies as anonymous, authenticated owner, authenticated non-owner, and privileged service contexts where applicable.
- Authentication is not authorization: a policy restricted only to `authenticated` is insufficient for user-owned data.
- Keep service-role access server-side. Grant the minimum schema, table, function, and storage privileges needed.
- Treat views, security-definer functions, triggers, and public functions as security-sensitive. Set ownership, search paths, invoker behavior, and execute grants intentionally.
- Model garments, images, outfits, catalog products, preferences, and generation artifacts with explicit ownership, provenance, lifecycle, and deletion behavior.
- Use constraints for invariants and indexes for observed access paths. Inspect query plans before asserting an optimization.
- Define transaction and concurrency behavior for multi-row writes. Use idempotency or uniqueness constraints for retried operations.
- Keep private data out of exposed schemas when public API access is unnecessary, and apply storage policies consistent with row ownership.

Run migration, policy, and query verification before completion; do not rely on SQL review alone.
