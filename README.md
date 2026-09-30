# Styley Server

FastAPI backend for Styley's wardrobe intake. The App sends a photo or short video to `POST /v1/wardrobe/analyze`, reviews the detected pieces, then saves selected pieces with `POST /v1/wardrobe/items`. Saved items are private in Supabase Storage and owned by the signed-in user in Postgres.

## Local setup

1. Install Python 3.12, FFmpeg, and the dependencies in `pyproject.toml`.
2. Apply `supabase/migrations/20260929181933_wardrobe_items.sql` to a new Supabase project. It was applied to the Styley project on September 29, 2026.
3. Complete the ignored local `.env` file using `.env.example` as a guide. The server loads this file at startup, while deployment environment variables take precedence. Set `GOOGLE_CLOUD_PROJECT` to the Cloud project ID with the active credit and enable the Vertex AI API there. Keep the Supabase secret and Replicate token on this server only.
4. Provide Google Cloud Application Default Credentials: use `gcloud auth application-default login` for local development, or attach a service account with Vertex AI access to the deployed server. No Gemini API key is used.
5. Run `uvicorn app.main:app --reload` and configure the App's `SERVER_URL` with an HTTPS address reachable by the device.

Gemini 2.5 Flash on Vertex AI detects up to eight garments in each sampled frame, returning bounding boxes, editable details, and whether each item is worn. Gemini 3.1 Flash Image isolates worn garments into catalog images. These calls are billed to the configured Google Cloud project, where eligible Cloud trial credits can apply. FFmpeg samples up to twelve frames from the first twelve seconds of video; similar crops are deduplicated. The user chooses which results to save, then edits details on the item screen.

Flat and hanging garments use Bria Background Removal on Replicate for transparent catalog images. Set `REPLICATE_API_TOKEN` in the local `.env`; Replicate billing must be enabled for paid model calls. The model is listed at $0.018 per output image, so the current cap of 20 candidates per upload costs at most $0.36 in background removal calls, before any other provider charges. If enhancement fails, the user receives an editable crop.

Ximilar is no longer part of the wardrobe intake path. Google Cloud Application Default Credentials are still needed to run Gemini locally or on the deployed server.

## API contract

All wardrobe routes require `Authorization: Bearer <Supabase access token>`. The server checks each token with Supabase Auth. The API never accepts a user ID from the App.

| Route | Body | Result |
| --- | --- | --- |
| `POST /v1/wardrobe/analyze` | multipart `file`, JPEG/PNG/HEIC image up to 10 MB or MP4/QuickTime video up to 30 MB | `{ "candidates": [...] }`, including editable metadata and base64 preview images |
| `POST /v1/wardrobe/items` | selected candidate metadata, `candidate_id`, `source`, `image_mime`, `image_base64` | saved item with signed image URL |
| `GET /v1/wardrobe/items` | none | newest 100 owned items with signed image URLs |
| `PATCH /v1/wardrobe/items/{id}` | editable metadata | updated owned item, or 404 |

The candidate ID is the idempotency key for save retries. Provider keys, source media, and provider payloads are never returned. Image previews remain in device memory until saved. Signed image links expire after 24 hours; the App should refresh the wardrobe when reopening it.

## Checks

`ruff check app tests` and `pytest -q` cover schema mapping, review candidates, token gates, and owner-scoped updates. The Styley Supabase migration was applied and verified. End-to-end provider calls still require configured credentials and a test project.

## Google Cloud Run deployment

Run `python scripts/deploy_cloud_run.py` from this checkout after installing the Server dependencies and signing in with `gcloud auth login`. The script deploys `styley-server` in Tel Aviv (`me-west1`) from this checkout. It enables the required Google APIs and creates separate identities for running and building the service. The runtime identity has Vertex AI User access; the build identity has Cloud Run Builder access. It stores the Supabase secret and Replicate token as version-pinned Secret Manager values and grants the runtime identity access only to those two secrets. It then builds the Dockerfile and deploys the service with one CPU, 1 GiB of memory, no minimum instances, and at most two instances.

The deployed service URL is `https://styley-server-448199079489.me-west1.run.app`. Use `python scripts/deploy_cloud_run.py --reuse-secrets` for code-only redeployments that should keep the currently enabled Secret Manager versions. Omit the flag when rotating the values from the local `.env` file.

The Cloud Run service is connected directly to `styleyapp/Server` through Cloud Run's **Connect to repo** setting. A Cloud Build trigger watches the `main` branch, builds the root `Dockerfile`, pushes the image to Artifact Registry, and updates the existing `styley-server` service. The deployment retains the service's environment variables, pinned Secret Manager versions, and runtime identity. Run `ruff check app tests scripts` and `pytest -q` before pushing changes; the Cloud Run trigger builds and deploys but does not run these checks.

Cloud Run supplies Google credentials to the service account automatically. Never upload the local Google credential file or set `GOOGLE_APPLICATION_CREDENTIALS` in Cloud Run. The source upload excludes `.env`, Git data, tests, and local caches. The service is publicly reachable so the mobile App can call it, while each wardrobe route still requires a valid Supabase user access token. `/health` is public for monitoring. Set the App's `SERVER_URL` to the HTTPS service URL returned by deployment.
