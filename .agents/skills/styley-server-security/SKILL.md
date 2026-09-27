---
name: styley-server-security
description: Implement or review Styley Server authentication, authorization, secrets, privacy, validation, logging, abuse prevention, and security-sensitive integrations. Use for security or user-data work.
---

# Styley Server Security

Enforce identity, ownership, role, and operation authorization at trusted server and database boundaries. Never rely on App validation, hidden controls, or route guards.

- Validate tokens with the intended issuer, audience, signature, expiry, and session behavior. Handle revocation and account deletion explicitly.
- Derive authorization from trusted server data or app metadata, never user-editable profile metadata.
- Keep service-role, model-provider, commerce, and integration secrets out of source, responses, logs, fixtures, and client-visible configuration. Commit only safe `.env.example` names.
- Validate type, size, format, and ownership of every external input, upload, callback, webhook, model output, and tool argument.
- Verify webhook signatures and replay protections before performing mutations.
- Apply rate, cost, size, and concurrency limits to authentication, uploads, search, generation, and other abuse-sensitive paths.
- Redact tokens, personal measurements, wardrobe media, precise location, conversations, prompts, purchase data, and signed URLs from routine logs and traces.
- Define retention, export, and deletion behavior for user data and derived AI artifacts. Avoid collecting data that is not required.
- Use least privilege for database roles, storage buckets, network access, and third-party credentials.

Security fixes require a focused regression test or reproducible verification of the protected boundary.
