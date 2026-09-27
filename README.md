# Styley Server

The independent backend repository for Styley. It is planned around FastAPI and Supabase and will own authentication, authorization, domain rules, API contracts, data access, and server-side AI orchestration.

Backend implementation will be added after the initial domain and API contracts are defined.

## Structure

```text
app/
├── api/             HTTP and realtime API composition
├── core/            Configuration, security, and observability
├── domains/         Auth, users, wardrobe, catalog, and styling slices
├── integrations/    Supabase, AI, weather, and commerce adapters
└── workers/         Background jobs
supabase/
└── migrations/      Ordered database migrations
tests/
├── unit/
├── integration/
└── contract/
```

Dependencies and the executable FastAPI entry point will be added with the first defined API vertical slice rather than locking unused framework versions into the scaffold.
