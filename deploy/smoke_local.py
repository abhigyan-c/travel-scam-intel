"""Create synthetic secret files for an isolated production-profile smoke test.

No provider keys are loaded. Run from repository root; files stay in ignored .tools.
"""

from pathlib import Path

VALUES = {
    "POSTGRES_PASSWORD": "synthetic-smoke-only",
    "DATABASE_URL": (
        "postgresql+psycopg://travel:synthetic-smoke-only@postgres:5432/travel_scam_intel"
    ),
    "REDIS_URL": "redis://redis:6379/0",
    "S3_ACCESS_KEY": "travel-local",
    "S3_SECRET_KEY": "travel-local-password",
    "SERPER_API_KEY": "",
    "SARVAM_API_KEY": "",
    "ADMIN_API_TOKEN": "synthetic-smoke-only",
}


if __name__ == "__main__":
    directory = Path(".tools/production-smoke-secrets")
    directory.mkdir(parents=True, exist_ok=True)
    for key, value in VALUES.items():
        (directory / key).write_text(value, encoding="utf-8")
