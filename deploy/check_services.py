"""Opt-in live checks using local dotenv; outputs metadata and status, never values.

Creates/deletes a unique synthetic R2 object in each configured bucket. Bitwarden
access is read-only. Run: python -m deploy.check_services
"""

import json
import os
import re
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import dotenv_values

from app.core.config import Settings
from app.integrations.storage import create_storage_client
from deploy.export_secrets import APP_KEYS, BACKUP_KEYS, KEY_ALIASES, select_secrets


def error_status(exc):
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, ClientError):
        return f"HTTP {exc.response['ResponseMetadata']['HTTPStatusCode']}"
    return type(exc).__name__


def vault_command(token, *arguments):
    command = (
        ["bws"]
        if shutil.which("bws")
        else ["docker", "run", "--rm", "-e", "BWS_ACCESS_TOKEN", "ghcr.io/bitwarden/bws"]
    )
    result = subprocess.run(
        [*command, *arguments],
        env={**os.environ, "BWS_ACCESS_TOKEN": token},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        if arguments[:2] == ("secret", "create") and "404" in result.stderr:
            raise PermissionError("vault_create_not_found_check_project_write_access")
        if re.search(r"403|forbidden|permission|write access|read.only", result.stderr, re.I):
            raise PermissionError("vault_access_denied")
        raise ValueError("vault_command_failed")
    return json.loads(result.stdout)


def check_vault(values):
    token = values["BITWARDEN_MACHINE_ACCESS_TOKEN"]
    outcomes = {}
    for label, project_key, required in [
        ("prod-app", "PROD_APP_UUID", APP_KEYS),
        ("prod-backup", "PROD_BACKUP_UUID", BACKUP_KEYS),
    ]:
        try:
            project = str(UUID(values[project_key]))
            metadata = vault_command(token, "project", "get", project)
            if metadata["id"] != project:
                raise ValueError("project_mismatch")
            payload = vault_command(token, "secret", "list", project)
            present = {KEY_ALIASES.get(item["key"], item["key"]) for item in payload}
            missing = sorted(required - present)
            result = {"access": "passed", "secret_count": len(payload), "missing": missing}
            if not missing:
                try:
                    select_secrets(payload, required)
                    result["required_values"] = "passed"
                except ValueError:
                    result["required_values"] = "empty_or_duplicate"
            outcomes[label] = result
        except Exception as exc:
            outcomes[label] = {"access": "failed", "error": error_status(exc)}
    return outcomes


def check_public_access(values, bucket):
    if not values.get("R2_TOKEN"):
        return {"public_configuration": "unverified_no_management_token"}
    account = urlsplit(values["R2_ENDPOINT"]).hostname.split(".")[0]
    base = f"https://api.cloudflare.com/client/v4/accounts/{account}/r2/buckets/{bucket}/domains"
    try:
        results = {}
        for kind in ("managed", "custom"):
            request = Request(
                f"{base}/{kind}", headers={"Authorization": f"Bearer {values['R2_TOKEN']}"}
            )
            with urlopen(request, timeout=15) as response:
                payload = json.load(response)
            if not payload.get("success"):
                raise ValueError("management_request_failed")
            results[kind] = payload["result"]
        managed_disabled = results["managed"].get("enabled") is False
        custom_disabled = all(
            domain.get("enabled") is False for domain in results["custom"]["domains"]
        )
        return {
            "public_configuration": "private"
            if managed_disabled and custom_disabled
            else "public_or_unverified"
        }
    except Exception as exc:
        return {"public_configuration": "unverified", "management_error": error_status(exc)}


def check_bucket(client, anonymous, bucket):
    key = f"_deployment-checks/{uuid4()}.txt"
    body = b"Travel scam deployment connectivity check. Synthetic data only."
    created = False
    result = {}
    try:
        client.head_bucket(Bucket=bucket)
        client.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch="*")
        created = True
        response = client.get_object(Bucket=bucket, Key=key)
        with response["Body"] as stream:
            if stream.read() != body:
                raise ValueError("roundtrip_mismatch")
        result["roundtrip"] = "passed"
        try:
            client.put_object(Bucket=bucket, Key=key, Body=b"must not overwrite", IfNoneMatch="*")
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
            result["conditional_write"] = "passed"
        else:
            raise ValueError("conditional_write_not_enforced")
        response = client.get_object(Bucket=bucket, Key=key)
        with response["Body"] as stream:
            if stream.read() != body:
                raise ValueError("immutable_content_changed")
        try:
            response = anonymous.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            result["anonymous_s3_access"] = f"denied ({error_status(exc)})"
        else:
            response["Body"].close()
            raise ValueError("anonymous_read_allowed")
    except Exception as exc:
        result["error"] = error_status(exc)
    finally:
        if created:
            try:
                client.delete_object(Bucket=bucket, Key=key)
                try:
                    client.head_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                        raise
                else:
                    raise ValueError("temporary_object_still_exists")
                result["cleanup"] = "passed"
            except Exception as exc:
                result["cleanup"] = error_status(exc)
                result["cleanup_key"] = key
    return result


def main():
    values = dotenv_values(".env")
    required = {
        "R2_ENDPOINT",
        "R2_ACCESS",
        "R2_SECRET",
        "BITWARDEN_MACHINE_ACCESS_TOKEN",
        "PROD_APP_UUID",
        "PROD_BACKUP_UUID",
    }
    if any(not values.get(key) for key in required):
        raise SystemExit("Required local configuration is missing; see deployment runbook.")
    endpoint = values["R2_ENDPOINT"].rstrip("/")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".r2.cloudflarestorage.com")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise SystemExit("Expected an HTTPS R2 S3 account endpoint.")
    vault = check_vault(values)
    print(json.dumps({"bitwarden": vault}), flush=True)
    failed = any(result.get("required_values") != "passed" for result in vault.values())
    incomplete = False
    settings = Settings(
        _env_file=None,
        s3_endpoint=endpoint,
        s3_access_key=values["R2_ACCESS"],
        s3_secret_key=values["R2_SECRET"],
    )
    client = create_storage_client(settings)
    anonymous = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        config=Config(
            signature_version=UNSIGNED,
            connect_timeout=2,
            read_timeout=2,
            retries={"total_max_attempts": 1},
        ),
    )
    try:
        for bucket in ["travel-scam-objects-prod", "travel-scam-backups-prod"]:
            checks = check_bucket(client, anonymous, bucket)
            checks.update(check_public_access(values, bucket))
            failed = failed or "error" in checks or checks.get("cleanup") != "passed"
            failed = failed or checks["public_configuration"] == "public_or_unverified"
            incomplete = incomplete or checks["public_configuration"] != "private"
            print(
                json.dumps({"bucket": bucket, "checks": checks}),
                flush=True,
            )
    finally:
        client.close()
        anonymous.close()
    print(
        json.dumps({"verification": "failed" if failed else "partial" if incomplete else "passed"})
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
