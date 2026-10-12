"""Create/update the private Cloud Run maintenance job and its 15-minute scheduler.

Run AFTER deploying the Task 3 image. Reuses the deployed service's image,
identity, environment and pinned secrets. No private values are printed.
"""

import json
import subprocess

from scripts.deploy_cloud_run import PROJECT, REGION, SERVICE, SERVICE_ACCOUNT

JOB = "styley-maintenance"


def command(*args, capture=False):
    return subprocess.run(
        ["gcloud", *args, f"--project={PROJECT}"], capture_output=capture, check=True
    )


def configure():
    service = json.loads(
        command(
            "run",
            "services",
            "describe",
            SERVICE,
            f"--region={REGION}",
            "--format=json",
            capture=True,
        ).stdout
    )
    container = service["spec"]["template"]["spec"]["containers"][0]
    env = []
    secrets = []
    for value in container.get("env", []):
        if "value" in value:
            env.append(value["name"] + "=" + value["value"])
        else:
            secret = value["valueFrom"]["secretKeyRef"]
            secrets.append(value["name"] + "=" + secret["name"] + ":" + secret["key"])
    command("services", "enable", "cloudscheduler.googleapis.com", "run.googleapis.com")
    command(
        "run",
        "jobs",
        "deploy",
        JOB,
        f"--region={REGION}",
        f"--image={container['image']}",
        f"--service-account={SERVICE_ACCOUNT}",
        "--command=python",
        "--args=scripts/run_maintenance.py",
        "--task-timeout=300s",
        "--max-retries=2",
        "--tasks=1",
        "--parallelism=1",
        "--memory=512Mi",
        "--set-env-vars=^|^" + "|".join(env),
        f"--set-secrets={','.join(secrets)}",
        "--quiet",
    )
    command(
        "run",
        "jobs",
        "add-iam-policy-binding",
        JOB,
        f"--region={REGION}",
        f"--member=serviceAccount:{SERVICE_ACCOUNT}",
        "--role=roles/run.invoker",
        "--quiet",
    )
    uri = f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/jobs/{JOB}:run"
    present = (
        subprocess.run(
            [
                "gcloud",
                "scheduler",
                "jobs",
                "describe",
                JOB,
                f"--location={REGION}",
                f"--project={PROJECT}",
            ],
            capture_output=True,
        ).returncode
        == 0
    )
    command(
        "scheduler",
        "jobs",
        "update" if present else "create",
        "http",
        JOB,
        f"--location={REGION}",
        "--schedule=*/15 * * * *",
        "--time-zone=UTC",
        f"--uri={uri}",
        "--http-method=POST",
        f"--oauth-service-account-email={SERVICE_ACCOUNT}",
        "--message-body={}",
        "--quiet",
    )
    print("Private maintenance job scheduled every 15 minutes.")


if __name__ == "__main__":
    configure()
