"""Idempotently populate deployment projects; no values are printed or written locally.

Default is a dry run. Explicitly pass --apply to create missing secrets in Bitwarden.
Existing secrets are preserved, including when resuming an interrupted run.
"""

import argparse
import json
import secrets
from urllib.parse import quote
from uuid import UUID

from dotenv import dotenv_values

from deploy.check_services import vault_command
from deploy.export_secrets import KEY_ALIASES, select_secrets


def existing_values(payload):
    names = {KEY_ALIASES.get(item["key"], item["key"]) for item in payload}
    return select_secrets(payload, names)


def planned_values(local, existing, *, backup):
    values = {"S3_ACCESS_KEY": local["R2_ACCESS"], "S3_SECRET_KEY": local["R2_SECRET"]}
    if backup:
        values["BACKUP_PASSPHRASE"] = secrets.token_urlsafe(48)
    else:
        if "DATABASE_URL" in existing and "POSTGRES_PASSWORD" not in existing:
            raise ValueError("existing_database_url_requires_matching_password")
        password = existing.get("POSTGRES_PASSWORD") or secrets.token_urlsafe(36)
        values.update(
            {
                "POSTGRES_PASSWORD": password,
                "DATABASE_URL": (
                    f"postgresql+psycopg://travel:{quote(password, safe='')}"
                    "@postgres:5432/travel_scam_intel"
                ),
                "REDIS_URL": "redis://redis:6379/0",
                "SERPER_API_KEY": local["SERPER_API_KEY"],
                "SARVAM_API_KEY": local["SARVAM_API_KEY"],
                "ADMIN_API_TOKEN": secrets.token_urlsafe(48),
            }
        )
    return {key: value for key, value in values.items() if key not in existing}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    local = dotenv_values(".env")
    try:
        for key in (
            "R2_ACCESS",
            "R2_SECRET",
            "SERPER_API_KEY",
            "SARVAM_API_KEY",
            "BITWARDEN_MACHINE_ACCESS_TOKEN",
            "PROD_APP_UUID",
            "PROD_BACKUP_UUID",
        ):
            if not local.get(key):
                raise ValueError("missing_local_configuration")
        token = local["BITWARDEN_MACHINE_ACCESS_TOKEN"]
        for label, project_key, backup in [
            ("prod-app", "PROD_APP_UUID", False),
            ("prod-backup", "PROD_BACKUP_UUID", True),
        ]:
            project = str(UUID(local[project_key]))
            if vault_command(token, "project", "get", project)["id"] != project:
                raise ValueError("project_mismatch")
            existing = existing_values(vault_command(token, "secret", "list", project))
            planned = planned_values(local, existing, backup=backup)
            print(
                json.dumps(
                    {
                        "project": label,
                        "create": sorted(planned),
                        "mode": "apply" if args.apply else "dry_run",
                    }
                ),
                flush=True,
            )
            if args.apply:
                for key, value in planned.items():
                    vault_command(token, "secret", "create", key, value, project)
                saved = existing_values(vault_command(token, "secret", "list", project))
                if any(saved.get(key) != value for key, value in planned.items()):
                    raise ValueError("vault_readback_mismatch")
                print(
                    json.dumps(
                        {"project": label, "readback": "passed", "created_count": len(planned)}
                    ),
                    flush=True,
                )
    except PermissionError:
        raise SystemExit("Vault access denied. Check machine-account write permissions.") from None
    except Exception:
        raise SystemExit(
            "Vault setup failed; existing entries were preserved. "
            "Check permissions and configuration."
        ) from None


if __name__ == "__main__":
    main()
