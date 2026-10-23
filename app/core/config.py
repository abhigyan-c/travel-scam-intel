from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    name: str = Field(default="Travel Scam Intelligence", min_length=1)
    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr = Field(
        default="postgresql+psycopg://travel:travel-local@127.0.0.1:5432/travel_scam_intel",
        validation_alias="DATABASE_URL",
    )
    redis_url: SecretStr = Field(default="redis://127.0.0.1:6379/0", validation_alias="REDIS_URL")
    s3_endpoint: str = Field(default="http://127.0.0.1:9000", validation_alias="S3_ENDPOINT")
    s3_bucket: str = Field(default="raw-documents", min_length=1, validation_alias="S3_BUCKET")
    s3_access_key: SecretStr = Field(default="travel-local", validation_alias="S3_ACCESS_KEY")
    s3_secret_key: SecretStr = Field(
        default="travel-local-password", validation_alias="S3_SECRET_KEY"
    )
    readiness_timeout_seconds: float = Field(default=3, gt=0, le=30)
    serper_api_key: SecretStr = Field(default="", validation_alias="SERPER_API_KEY")
