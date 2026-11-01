import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from deploy import export_secrets
from deploy.export_secrets import APP_KEYS, select_secrets


def test_runtime_secrets_are_loaded_by_alias_and_environment_wins(tmp_path, monkeypatch):
    (tmp_path / "DATABASE_URL").write_text("postgresql+psycopg://secret@postgres/db")
    (tmp_path / "SARVAM_API_KEY").write_text("secret-vault-value")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    settings = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert settings.database_url.get_secret_value() == "postgresql+psycopg://secret@postgres/db"
    assert settings.sarvam_api_key.get_secret_value() == "secret-vault-value"
    assert "secret-vault-value" not in repr(settings)
    monkeypatch.setenv("SARVAM_API_KEY", "override")
    assert (
        Settings(_env_file=None, _secrets_dir=tmp_path).sarvam_api_key.get_secret_value()
        == "override"
    )


def test_vault_export_requires_complete_unique_allowlisted_values():
    payload = [{"key": key, "value": "synthetic"} for key in APP_KEYS]
    assert (
        set(select_secrets(payload + [{"key": "../../escape", "value": "ignored"}], APP_KEYS))
        == APP_KEYS
    )
    for invalid in [
        payload[:-1],
        payload + [payload[0]],
        [{**item, "value": ""} for item in payload],
    ]:
        with pytest.raises(ValueError):
            select_secrets(invalid, APP_KEYS)


@pytest.mark.parametrize(
    "values",
    [
        {"worker_max_jobs": 0},
        {"worker_max_jobs": 5},
        {"database_pool_size": 0},
        {"database_max_overflow": -1},
    ],
)
def test_production_resource_bounds(values):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **values)


def test_export_checks_vault_before_writing_and_assigns_service_owners(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("synthetic-token")
    destination = tmp_path / "runtime"
    ownership = {}
    monkeypatch.setattr(
        export_secrets.os,
        "chown",
        lambda path, uid, gid: ownership.update({path.name: uid}),
        raising=False,
    )
    response = SimpleNamespace(returncode=1, stdout="", stderr="sensitive-provider-output")
    monkeypatch.setattr(export_secrets.subprocess, "run", lambda *args, **kwargs: response)
    project = "00000000-0000-4000-8000-000000000001"
    with pytest.raises(ValueError, match="vault_fetch_failed"):
        export_secrets.export(project, token, destination, APP_KEYS)
    assert not destination.exists()
    response.returncode = 0
    response.stdout = json.dumps([{"key": key, "value": "synthetic"} for key in APP_KEYS])
    export_secrets.export(project, token, destination, APP_KEYS)
    assert ownership["POSTGRES_PASSWORD"] == 999
    assert ownership["DATABASE_URL"] == 10001
    assert {file.name for file in destination.iterdir()} == APP_KEYS
    with pytest.raises(ValueError, match="destination_must_be_new"):
        export_secrets.export(project, token, destination, APP_KEYS)
