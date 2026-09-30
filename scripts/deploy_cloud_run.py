"""Deploy the local Server checkout to Styley's Google Cloud Run project."""

import argparse
import json
import subprocess
from pathlib import Path

from dotenv import dotenv_values

PROJECT = "project-9c6ace89-27fc-4cd0-85d"
REGION = "me-west1"
SERVICE = "styley-server"
SERVICE_ACCOUNT = f"styley-server@{PROJECT}.iam.gserviceaccount.com"
BUILD_ACCOUNT = f"styley-server-build@{PROJECT}.iam.gserviceaccount.com"
ROOT = Path(__file__).resolve().parents[1]
SECRETS = {
    "SUPABASE_SECRET_KEY": "styley-supabase-secret-key",
    "REPLICATE_API_TOKEN": "styley-replicate-api-token",
}


def gcloud(*args: str, input_data: bytes | None = None, capture: bool = False):
    return subprocess.run(
        ["gcloud", *args, f"--project={PROJECT}"],
        cwd=ROOT,
        input=input_data,
        capture_output=capture,
        check=True,
    )


def exists(*args: str) -> bool:
    result = subprocess.run(
        ["gcloud", *args, f"--project={PROJECT}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def main(*, reuse_secrets: bool = False) -> None:
    settings = dotenv_values(ROOT / ".env")
    required = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", *SECRETS)
    missing = [name for name in required if not settings.get(name)]
    if missing:
        raise SystemExit(f"Missing local .env settings: {', '.join(missing)}")

    gcloud(
        "services",
        "enable",
        "run.googleapis.com",
        "cloudbuild.googleapis.com",
        "artifactregistry.googleapis.com",
        "secretmanager.googleapis.com",
        "iam.googleapis.com",
        "aiplatform.googleapis.com",
    )

    if not exists("iam", "service-accounts", "describe", BUILD_ACCOUNT):
        gcloud(
            "iam",
            "service-accounts",
            "create",
            "styley-server-build",
            "--display-name=Styley Server build identity",
        )
    gcloud(
        "projects",
        "add-iam-policy-binding",
        PROJECT,
        f"--member=serviceAccount:{BUILD_ACCOUNT}",
        "--role=roles/run.builder",
        "--format=none",
        "--quiet",
    )

    if not exists("iam", "service-accounts", "describe", SERVICE_ACCOUNT):
        gcloud(
            "iam",
            "service-accounts",
            "create",
            "styley-server",
            "--display-name=Styley Server Cloud Run identity",
        )
    gcloud(
        "projects",
        "add-iam-policy-binding",
        PROJECT,
        f"--member=serviceAccount:{SERVICE_ACCOUNT}",
        "--role=roles/aiplatform.user",
        "--format=none",
        "--quiet",
    )

    versions = {}
    for env_name, secret_name in SECRETS.items():
        if not exists("secrets", "describe", secret_name):
            if reuse_secrets:
                raise RuntimeError(f"Cannot reuse missing secret: {secret_name}")
            gcloud("secrets", "create", secret_name, "--replication-policy=automatic")
        if reuse_secrets:
            version = gcloud(
                "secrets",
                "versions",
                "list",
                secret_name,
                "--filter=state:enabled",
                "--sort-by=~createTime",
                "--limit=1",
                "--format=value(name)",
                capture=True,
            )
            versions[env_name] = version.stdout.decode().strip().rsplit("/", 1)[-1]
            if not versions[env_name]:
                raise RuntimeError(f"No enabled version for secret: {secret_name}")
        else:
            version = gcloud(
                "secrets",
                "versions",
                "add",
                secret_name,
                "--data-file=-",
                "--format=json(name)",
                input_data=settings[env_name].encode(),
                capture=True,
            )
            versions[env_name] = json.loads(version.stdout)["name"].rsplit("/", 1)[-1]
        gcloud(
            "secrets",
            "add-iam-policy-binding",
            secret_name,
            f"--member=serviceAccount:{SERVICE_ACCOUNT}",
            "--role=roles/secretmanager.secretAccessor",
            "--format=none",
            "--quiet",
        )

    public_env = ",".join(
        (
            f"SUPABASE_URL={settings['SUPABASE_URL']}",
            f"SUPABASE_PUBLISHABLE_KEY={settings['SUPABASE_PUBLISHABLE_KEY']}",
            f"GOOGLE_CLOUD_PROJECT={PROJECT}",
            "GOOGLE_CLOUD_LOCATION=global",
        )
    )
    secret_env = ",".join(
        f"{env_name}={secret_name}:{versions[env_name]}"
        for env_name, secret_name in SECRETS.items()
    )
    gcloud(
        "run",
        "deploy",
        SERVICE,
        "--source=.",
        f"--build-service-account=projects/{PROJECT}/serviceAccounts/{BUILD_ACCOUNT}",
        f"--region={REGION}",
        f"--service-account={SERVICE_ACCOUNT}",
        f"--set-env-vars={public_env}",
        f"--set-secrets={secret_env}",
        "--allow-unauthenticated",
        "--memory=1Gi",
        "--cpu=1",
        "--concurrency=4",
        "--max-instances=2",
        "--min-instances=0",
        "--timeout=300s",
        "--quiet",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-secrets", action="store_true")
    try:
        main(reuse_secrets=parser.parse_args().reuse_secrets)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Google Cloud command failed with status {error.returncode}") from None
