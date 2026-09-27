---
name: styley-server-testing-production
description: Add or review Styley Server tests, CI, observability, reliability, rollout, migrations, and production readiness. Use for behavior changes, infrastructure work, or releases.
---

# Styley Server Testing Production

Test the highest-risk observable behavior rather than maximizing coverage mechanically.

- Unit test domain invariants, ranking constraints, mappings, idempotency, and failure classification.
- Integration test routes through real validation and dependency boundaries, including authentication, authorization, transactions, and stable error contracts.
- Verify migrations forward on representative schema state. Test RLS and storage policies with distinct roles and ownership scenarios.
- Contract-test App-facing schemas and realtime events, including compatibility, pagination, duplicate delivery, ordering, reconnect, and cancellation.
- For external services and AI, test timeouts, malformed results, bounded retries, partial failure, fallback, and cost or concurrency limits. Keep live tests separate and opt-in.
- CI should run formatting, linting, type checks, tests, migration validation, and dependency or secret scanning appropriate to the checked-in stack.
- Use structured logs, metrics, traces, and health/readiness checks without leaking sensitive data. Alert on user impact and exhausted recovery, not routine noise.
- Plan backward-compatible deployment order for contract and schema changes. Use expand/migrate/contract for destructive changes and document rollback limits.
- Gate risky AI, recommendation, commerce, or ingestion changes when staged rollout materially reduces harm.

Before completion, report exactly which checks ran, which environment they covered, and what remains unverified.
