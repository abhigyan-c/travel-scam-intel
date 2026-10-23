import asyncio
import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.documents import create_document
from app.integrations.fetch import Fetcher, canonicalize_url
from app.models.document import Document
from app.models.source import Source, SourceType


class DiscoveryResult(BaseModel):
    url: str
    title: str | None = None
    source_type: SourceType = SourceType.OTHER
    published_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return canonicalize_url(value)


def _store_raw(storage: BaseClient, bucket: str, key: str, page):
    try:
        storage.put_object(
            Bucket=bucket,
            Key=key,
            Body=page.content,
            ContentType=page.content_type,
            IfNoneMatch="*",
        )
    except ClientError as exc:
        if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
            raise


async def ingest_url(
    session: AsyncSession,
    result: DiscoveryResult,
    *,
    fetcher: Fetcher,
    storage: BaseClient,
    bucket: str,
) -> Document:
    page = await fetcher.fetch(result.url)
    content_hash = hashlib.sha256(page.content).hexdigest()
    # Deterministic keys make retries safe after storage succeeds but the DB transaction fails.
    url_hash = hashlib.sha256(result.url.encode()).hexdigest()
    key = f"raw/{url_hash}/{content_hash}"
    await asyncio.to_thread(_store_raw, storage, bucket, key, page)
    acquired_at = datetime.now(UTC)
    async with session.begin_nested():
        source_id = await session.scalar(
            insert(Source)
            .values(
                platform=urlsplit(result.url).hostname,
                canonical_url=result.url,
                source_type=result.source_type,
                published_at=result.published_at,
                retrieved_at=acquired_at,
            )
            .on_conflict_do_nothing(index_elements=[Source.canonical_url])
            .returning(Source.id)
        )
        if source_id is None:
            source_id = await session.scalar(
                select(Source.id).where(Source.canonical_url == result.url)
            )
        document = await create_document(
            session,
            source_id=source_id,
            raw_object_path=key,
            content_hash=content_hash,
            url=page.url,
            title=result.title,
            published_at=result.published_at,
            retrieved_at=acquired_at,
            document_metadata={"content_type": page.content_type},
        )
    return document
