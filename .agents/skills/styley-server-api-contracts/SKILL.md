---
name: styley-server-api-contracts
description: Design, implement, or review Styley Server REST and realtime API contracts, validation, versioning, pagination, compatibility, idempotency, and errors. Use for consumer-facing endpoints or events.
---

# Styley Server Api Contracts

Treat API contracts as Server-owned product interfaces. Define the contract before requiring App changes.

- Use typed request and response schemas with explicit validation. Do not expose database rows, provider payloads, internal exception text, or model-native output.
- Keep resource names and semantics stable. Distinguish wardrobe-owned items from purchasable catalog items in the schema, not only in UI convention.
- Return a consistent error envelope with stable machine codes, safe messages, field errors when relevant, and a trace identifier for diagnostics.
- Specify authentication, authorization, nullability, defaults, ordering, timestamps, units, locale-sensitive inputs, and ownership for every endpoint.
- Paginate unbounded collections with deterministic ordering and opaque cursors when mutation during traversal is likely.
- Require idempotency keys for retryable create or generation operations where duplicate execution would be harmful or costly.
- Document realtime event names, payload versions, ordering, deduplication identifiers, resume behavior, and snapshot reconciliation.
- Preserve backward compatibility unless a breaking change and coordinated rollout are explicitly authorized. Additive fields should remain safe for older clients.
- Define cancellation and partial-result semantics for long-running outfit generation.

Update contract documentation and contract tests in the same change as implementation.
