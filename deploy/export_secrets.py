"""Fetch an allowlisted Bitwarden project into a new protected runtime directory.

Run on the Linux host as root. Never prints provider output or secret values.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

APP_KEYS = {
    "DATABASE_URL",
    "REDIS_URL",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "SERPER_API_KEY",
    "SARVAM_API_KEY",
    "ADMIN_API_TOKEN",
    "POSTGRES_PASSWORD",
}
BACKUP_KEYS = {"S3_ACCESS_KEY", "S3_SECRET_KEY", "BACKUP_PASSPHRASE"}


def select_secrets(payload, required):
    result = {}
    for item in payload:
        key = item["key"]
        if key not in required:
            continue
        if key in result or not isinstance(item["value"], str) or not item["value"].strip():
            raise ValueError("duplicate_or_empty_secret")
        result[key] = item["value"]
    if result.keys() != required:
        raise ValueError("required_secrets_missing")
    return result


def export(project, token_file, destination, required):
    UUID(project)
    # Exclusive creation prevents overwriting files currently mounted in containers.
    if destination.exists():
        raise ValueError("destination_must_be_new")
    token = token_file.read_text().strip()
    if not token:
        raise ValueError("missing_machine_token")
    environment = {**os.environ, "BWS_ACCESS_TOKEN": token}
    completed = subprocess.run(
        ["bws", "secret", "list", project],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode:
        raise ValueError("vault_fetch_failed")
    values = select_secrets(json.loads(completed.stdout), required)
    destination.mkdir(mode=0o700, parents=False)
    # The app runs as uid 10001; PostgreSQL reads only its explicitly mounted file.
    os.chown(destination, 10001, 10001)
    for key, value in values.items():
        target = destination / key
        with target.open("x", encoding="utf-8", newline="") as stream:
            os.chmod(target, 0o400)
            stream.write(value)
        owner = 999 if key == "POSTGRES_PASSWORD" else 10001
        os.chown(target, owner, owner)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("token_file", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--backup", action="store_true")
    args = parser.parse_args()
    try:
        export(
            args.project,
            args.token_file,
            args.destination,
            BACKUP_KEYS if args.backup else APP_KEYS,
        )
    except Exception:
        raise SystemExit("Secret export failed; check access and required key names.") from None


if __name__ == "__main__":
    main()
