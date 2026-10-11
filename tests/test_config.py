import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_defaults_need_no_external_credentials(monkeypatch):
    for key in ("APP_NAME", "APP_ENVIRONMENT", "APP_LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.name == "Travel Scam Intelligence"
    assert settings.environment == "local"
    assert settings.log_level == "INFO"


def test_environment_overrides_dotenv_and_unknown_keys_are_ignored(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("APP_NAME=From file\nAPP_ENVIRONMENT=test\nFUTURE_API_KEY=placeholder\n")
    monkeypatch.setenv("APP_NAME", "From environment")
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_LOG_LEVEL", raising=False)
    settings = Settings(_env_file=dotenv)
    assert settings.name == "From environment"
    assert settings.environment == "test"


@pytest.mark.parametrize(
    "invalid", [{"name": ""}, {"environment": "unknown"}, {"log_level": "invalid"}]
)
def test_invalid_settings_fail_early(invalid):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **invalid)
