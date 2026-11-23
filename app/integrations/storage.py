import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings


def create_storage_client(settings: Settings) -> BaseClient:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=2,
            read_timeout=2,
            retries={"total_max_attempts": 1},
        ),
    )


def ensure_bucket(client: BaseClient, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
            raise
        try:
            client.create_bucket(Bucket=bucket)
        except ClientError as create_exc:
            if create_exc.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                raise


if __name__ == "__main__":
    settings = Settings()
    client = create_storage_client(settings)
    try:
        ensure_bucket(client, settings.s3_bucket)
    finally:
        client.close()
