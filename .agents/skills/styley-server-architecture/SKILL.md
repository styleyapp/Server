---
name: styley-server-architecture
description: Structure or review Styley's FastAPI backend, domain modules, dependency direction, services, integrations, and background work. Use for new server features or refactors.
---

# Styley Server Architecture

Organize the backend around Styley domains and use cases rather than one large routes, services, or utils layer. Add structure in proportion to actual implementation; the repository is currently a scaffold.

- FastAPI routes translate protocol input and output, invoke application services, and map known failures. They do not contain domain decisions or raw database orchestration.
- Domain code models wardrobe, garments, outfits, contexts, preferences, catalog items, and recommendation meaning without depending on FastAPI, Supabase, or model-provider payloads.
- Application services coordinate use cases, transactions, idempotency, and integration boundaries.
- Infrastructure owns Supabase, storage, external catalog, weather, commerce, and model-provider adapters. Map their shapes at the boundary.
- Use explicit dependency injection and lifecycle ownership. Avoid mutable global clients, hidden singletons, circular imports, god services, and catch-all helpers.
- Background jobs must be idempotent where practical, observable, bounded, and safe to retry according to operation semantics.
- Keep App and DesignSystem independent. Server modules expose documented contracts and never import client code.

Choose simple synchronous or asynchronous execution based on measured I/O and concurrency needs, not fashion.
