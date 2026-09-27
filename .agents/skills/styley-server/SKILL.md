---
name: styley-server
description: Design, build, change, debug, or review Styley's FastAPI and Supabase backend. Use for work inside the server repository; do not use for Flutter UI or DesignSystem implementation.
---

# Styley Server

Treat `server/` as an independent repository. Keep changes inside it unless the user explicitly asks for coordinated work in another Styley repository.

The Server is authoritative for authentication, authorization, business rules, domain models, wardrobe and catalog data, outfit generation and ranking, recommendations, API contracts, database access, AI orchestration, privileged integrations, and user-data behavior. It does not own Flutter UI, navigation, presentation state, device-only behavior, or DesignSystem components.

Load the focused skills relevant to the task:

- `styley-server-architecture` for FastAPI structure, domain boundaries, dependencies, and background work.
- `styley-server-api-contracts` for REST, realtime, schemas, compatibility, pagination, and errors.
- `styley-server-data-supabase` for Postgres, Supabase, migrations, RLS, storage, and query behavior.
- `styley-server-security` for authentication, authorization, secrets, privacy, logging, and abuse controls.
- `styley-server-ai` for outfit intelligence, model providers, prompts, tools, structured output, and streaming.
- `styley-server-testing-production` for tests, CI, observability, rollout, reliability, and release checks.

The intended stack is FastAPI with Supabase. Confirm checked-in versions and configuration before relying on a specific API. Publish explicit contracts for consumers and never depend on App or DesignSystem internals.
