# Styley Server

FastAPI backend for Styley's wardrobe intake, account preferences, and owned outfits. The App sends a photo or short video to `POST /v1/wardrobe/analyze`, reviews the detected pieces, then saves selected pieces with `POST /v1/wardrobe/items`. Saved items are private in Supabase Storage and owned by the signed-in user in Postgres.

## Local setup

1. Install Python 3.12, FFmpeg, and the dependencies in `pyproject.toml`.
2. Apply all files in `supabase/migrations/` in version order to a new Supabase project. The Styley project has these migrations applied, including persistent outfits on October 4, 2026.
3. Complete the ignored local `.env` file using `.env.example` as a guide. The server loads this file at startup, while deployment environment variables take precedence. Set `GOOGLE_CLOUD_PROJECT` to the Cloud project ID with the active credit and enable the Vertex AI API there. Keep the Supabase secret and Replicate token on this server only.
4. Provide Google Cloud Application Default Credentials: use `gcloud auth application-default login` for local development, or attach a service account with Vertex AI access to the deployed server. No Gemini API key is used.
5. Run `uvicorn app.main:app --reload` and configure the App's `SERVER_URL` with an HTTPS address reachable by the device.

Gemini 2.5 Flash on Vertex AI detects up to eight garments in each sampled frame, returning bounding boxes, English and Hebrew editable details/tags, garment length, and whether each item is worn. Gemini 3.1 Flash Image turns every detected garment into a centered, front-facing catalog photograph on white, straightening accidental folds and perspective while preserving the garment’s color, pattern, logos, cut, and construction. These calls are billed to the configured Google Cloud project, where eligible Cloud trial credits can apply. FFmpeg samples up to twelve frames from the first twelve seconds of video; similar crops are deduplicated. The user chooses which results to save, then edits details on the item screen.

Flat, hanging, crumpled, and worn garments all use the same catalog generation path before review. Bria on Replicate removes the background from the generated catalog photograph. Review and saved PNGs preserve alpha transparency, including white garment fabric. Background removal failure returns a recoverable error instead of substituting the white image. If generation fails or returns an unreadable image, analysis returns a recoverable 502 error so the App can offer retry; it never silently substitutes the photographed crop. Successful candidate previews are the same images submitted when the user saves selected pieces.

Ximilar is no longer part of the wardrobe intake path. Google Cloud Application Default Credentials are still needed to run Gemini locally or on the deployed server.

## API contract

All wardrobe, preferences, and outfit routes require `Authorization: Bearer <Supabase access token>`. The server checks each token with Supabase Auth. The API never accepts a user ID from the App.

| Route | Body | Result |
| --- | --- | --- |
| `POST /v1/wardrobe/analyze` | multipart `file`, JPEG/PNG/HEIC image up to 10 MB or MP4/QuickTime video up to 30 MB | `{ "candidates": [...] }`, including editable metadata and base64 catalog preview images; 502 if catalog generation fails |
| `POST /v1/wardrobe/items` | selected candidate metadata, `candidate_id`, `source`, `image_mime`, `image_base64` | saved item with signed image URL |
| `GET /v1/wardrobe/items` | none | newest 100 owned items with signed image URLs |
| `PATCH /v1/wardrobe/items/{id}` | editable metadata, optional `hebrew` label object and canonical `length` | updated owned item, or 404 |
| `PATCH /v1/wardrobe/items/{id}/favorite` | `{ "is_favorite": true/false }` | updated owned item, or 404; only changes favorite state |
| `DELETE /v1/wardrobe/items/{id}` | none | idempotent 204; only removes an owned item and attempts private image cleanup |

The candidate ID is the idempotency key for save retries. Provider keys, source media, and provider payloads are never returned. Image previews remain in device memory until saved. Signed image links expire after 24 hours; the App should refresh the wardrobe when reopening it.

## Checks

Install the pinned development tools with `python -m pip install '.[dev]'`.

`ruff check app tests` and `pytest -q` cover schema mapping, review candidates, token gates, and owner-scoped updates. The Styley Supabase migration was applied and verified. End-to-end provider calls still require configured credentials and a test project.

## Google Cloud Run deployment

Run `python scripts/deploy_cloud_run.py` from this checkout after installing the Server dependencies and signing in with `gcloud auth login`. The script deploys `styley-server` in Tel Aviv (`me-west1`) from this checkout. It enables the required Google APIs and creates separate identities for running and building the service. The runtime identity has Vertex AI User access; the build identity has Cloud Run Builder access. It stores the Supabase secret and Replicate token as version-pinned Secret Manager values and grants the runtime identity access only to those two secrets. It then builds the Dockerfile and deploys the service with one CPU, 1 GiB of memory, no minimum instances, and at most two instances.

The deployed service URL is `https://styley-server-448199079489.me-west1.run.app`. Use `python scripts/deploy_cloud_run.py --reuse-secrets` for code-only redeployments that should keep the currently enabled Secret Manager versions. Omit the flag when rotating the values from the local `.env` file.

The Cloud Run service is connected directly to `styleyapp/Server` through Cloud Run's **Connect to repo** setting. A Cloud Build trigger watches the `main` branch, builds the root `Dockerfile`, pushes the image to Artifact Registry, and updates the existing `styley-server` service. The deployment retains the service's environment variables, pinned Secret Manager versions, and runtime identity. Run `ruff check app tests scripts` and `pytest -q` before pushing changes; the Cloud Run trigger builds and deploys but does not run these checks.

Cloud Run supplies Google credentials to the service account automatically. Never upload the local Google credential file or set `GOOGLE_APPLICATION_CREDENTIALS` in Cloud Run. The source upload excludes `.env`, Git data, tests, and local caches. The service is publicly reachable so the mobile App can call it, while each wardrobe route still requires a valid Supabase user access token. `/health` is public for monitoring. Set the App's `SERVER_URL` to the HTTPS service URL returned by deployment.

### Bilingual metadata and favorites rollout

The migration `supabase/migrations/20261004053958_wardrobe_localization_and_favorites.sql` was applied to the Styley project on October 4, 2026. The deployed wardrobe Server was verified on October 4; ship the updated App after verifying native device behavior. The migration adds nullable `hebrew` labels, canonical `length` (`short`, `regular`, `long`, or unknown empty string), and `is_favorite` (default false); existing owner RLS policies remain in force. `hebrew` has the same label fields as English metadata. Both languages survive analyze, review, save, list, and edit. Older items without Hebrew labels fall back to their existing metadata; this migration does not translate or regenerate previously saved images. New scans require the existing `REPLICATE_API_TOKEN` configuration for transparent cutouts.

Deletion commits the owned row before storage cleanup. If private storage cleanup fails, the API still reports the completed item deletion and logs a generic warning; orphaned images require storage reconciliation. No signed URLs or image paths are logged.

### Persistent account preferences

`GET /v1/me/preferences` and `PUT /v1/me/preferences` require the same verified
Supabase session as wardrobe routes. The user ID is derived from that session;
request bodies cannot supply an owner. Responses are
`{"preferences": {...}, "revision": N}`. A missing profile returns defaults and
revision `0` without creating a row.

Preferences contain `closet_categories` (unique `men`/`women`, canonical order),
nullable `age` (13–65; 65 is the onboarding slider's 65+ choice), `top_fit`
(`slim`, `regular`, `relaxed`), `pants_fit` (`skinny`, `straight`, `baggy`), and
`locale` (`en`, `he`). Unknown fields and invalid enum values are rejected.
A PUT contains `preferences` and the required integer `expected_revision`.

Saves compare the expected revision atomically at the database boundary.
Concurrent first saves use the unique owner key; concurrent updates filter by
owner and revision. Identical committed values are acknowledged without a new
write, so a lost-response retry is safe. A differing stale write returns `409`
with error code `conflict`; consumers must reload and let the user edit again.
The client must never automatically overwrite with a freshly fetched revision.

The migration `supabase/migrations/20261004053017_user_preferences.sql` was
applied to the Styley project on October 4, 2026. The deployed preferences routes passed live authenticated verification on
October 4. Both preferences and wardrobe localization/favorites migrations
are applied; the App release and native-device verification remain pending. Preferences add one
owner-scoped table, explicit client read-only grants, Server write grants,
validation constraints, and a changed-at trigger. Account deletion cascades to preferences. Existing wardrobe
contracts remain compatible. Rollback can remove the new routes from the App
and Server while retaining the additive table; avoid dropping user data.

`supabase/tests/user_preferences.sql` verifies role isolation, denied client
writes, invalid data, revision checks, timestamps, and account deletion. Run it
only against a fresh isolated database with the migration already applied:

```sh
psql "$TEST_DATABASE_URL" --set=ON_ERROR_STOP=1 --file=supabase/tests/user_preferences.sql
```

Fixtures roll back. Adapter and API behavior are covered by
`tests/test_preferences.py`, including race conditions and lost-response retries.


### Wardrobe migration verification

`supabase/tests/wardrobe_localization_and_favorites.sql` covers legacy insert
defaults, bilingual metadata, favorites, valid/invalid lengths and translations,
owner/non-owner/anonymous isolation, ownership reassignment denial, and Server
access. Run only on a fresh isolated database with both wardrobe migrations
applied; fixed fixtures and all writes roll back:

```sh
psql "$TEST_DATABASE_URL" --set=ON_ERROR_STOP=1 --file=supabase/tests/wardrobe_localization_and_favorites.sql
```

The migration was also verified after preferences on an isolated database with
an existing garment. Live verification confirmed the three existing wardrobe
items, their original fields, preferences data, policies, and grants were
unchanged. New fields received the expected defaults, and live reads were
checked as owner, unrelated account, and anonymous without changing user data.
Migration filenames match the versions recorded in the Styley database.


### Persistent owned outfits

The additive migration `supabase/migrations/20261004063336_persistent_outfits.sql`
is applied to Styley. Outfit code is deployed and passed authenticated live
verification on October 4, 2026. The updated App still needs native-device
verification before release. The old wardrobe and preferences contracts remain compatible. Rolling back the
new API/App code can retain the additive tables; do not drop saved user data.

| Route | Body | Result |
| --- | --- | --- |
| `POST /v1/outfits/generate` | `request_id` UUID, `context` with occasion/mood (1–80 characters) and notes (up to 500) | persisted bilingual outfit with 1–6 owned garment references |
| `GET /v1/outfits` | `limit` 1–50, default 20; optional opaque `cursor` | saved `items` and `next_cursor` using stable saved-time/ID ordering |
| `GET /v1/outfits/{id}` | none | current owned garment data plus historical snapshots; other owners receive 404 |
| `PUT /v1/outfits/{id}/save` | none | idempotently saved outfit; refuses first save if any source garment was deleted |

Generation uses up to 100 recent/favorite owned items, with bounded metadata,
and top/pants fit preferences. Source photos, signed URLs, age, and closet
categories are not sent to this call. Gemini 2.5 Flash returns structured
English/Hebrew titles, explanations, and owned IDs. The Server validates known
garment roles and requires a top/bottom pair or one-piece outfit. Incomplete
closets return `422 insufficient_wardrobe`, never fabricated pieces.

Generation version `outfit-v1` is recorded. The request ID and normalized
context form the replay contract: identical completed requests return the
stored result without AI; changed context returns `409 conflict`. Database
locks enforce one active generation per account, ten new requests per hour,
and at most three attempts per request. Each worker receives a 90-second lease;
expired workers cannot overwrite a recovered request. Model work is bounded
by a 35-second budget, with bounded network and credential requests.

Safe failure codes include `generation_in_progress`, `rate_limited`,
`wardrobe_changed`, and `provider_unavailable`. Responses use the existing safe
error envelope and trace IDs. Provider payloads and private metadata are not
logged. Save checks ownership and source existence atomically; lost-response
retries preserve the original save time. Saved outfits use current garment
metadata and images, with original snapshots and unavailable markers after
deletion. Outfits cascade on account deletion. Clients have owner-only SELECT
access and cannot mutate outfit tables or execute the write RPCs.

Outfit image URLs are batch signed for one hour. Refresh collection/detail
reads to renew them; image signing outages preserve metadata and use placeholders.
Unsaved generation cleanup, cross-restart draft recovery, broader wardrobe
selection/pagination, and quotas for scan calls remain production-readiness work.

`supabase/tests/outfits.sql` tests migration constraints, leases, quotas,
idempotency, garment deletion, owner isolation, denied client writes/RPCs, and
account deletion. Run only against a fresh isolated migrated database; fixtures
roll back. Backend tests include safe model contracts and mocked failure cases.

```sh
psql "$TEST_DATABASE_URL" --set=ON_ERROR_STOP=1 --file=supabase/tests/outfits.sql
PYTHONPATH=. python scripts/evaluate_outfits.py --live
```

The evaluation is opt-in and makes four billed Vertex AI calls against
synthetic casual separates, a single dress, an incomplete closet, and hostile
metadata. It does not write to the database. All four passed on October 4.
Deployed Cloud Run API endpoints also passed against live Supabase/Vertex AI using two
disposable accounts: replay/conflicts, saves, pagination, account isolation,
edits, and deletion races. All fixtures were removed. These checks do not
replace native-device verification or controlled-beta fashion-quality review.
