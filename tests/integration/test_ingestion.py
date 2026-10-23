import hashlib
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.ingestion import DiscoveryResult, ingest_url
from app.integrations.fetch import FetchedPage, FetchError
from app.integrations.storage import create_storage_client, ensure_bucket
from app.models import Document, Source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
def storage():
    settings = Settings()
    client = create_storage_client(settings)
    ensure_bucket(client, settings.s3_bucket)
    keys = []
    yield client, settings.s3_bucket, keys
    try:
        for key in set(keys):
            client.delete_object(Bucket=settings.s3_bucket, Key=key)
    finally:
        client.close()


async def test_ingestion_versions_and_storage_roundtrip(session, storage):
    client, bucket, keys = storage
    result = DiscoveryResult(url=f"https://example.org/{uuid4()}#fragment", title="Fixture")
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchedPage(result.url + "final", b"original", "text/html")
    first = await ingest_url(session, result, fetcher=fetcher, storage=client, bucket=bucket)
    keys.append(first.raw_object_path)
    original_id, source_id, acquired_at = first.id, first.source_id, first.retrieved_at
    original_etag = client.head_object(Bucket=bucket, Key=first.raw_object_path)["ETag"]
    first.clean_text = "Retained processing result"
    await session.flush()
    repeated = await ingest_url(session, result, fetcher=fetcher, storage=client, bucket=bucket)
    assert repeated.id == original_id and repeated.retrieved_at == acquired_at
    assert repeated.clean_text == "Retained processing result"
    assert client.head_object(Bucket=bucket, Key=first.raw_object_path)["ETag"] == original_etag
    assert repeated.source.canonical_url == result.url
    assert repeated.url == result.url + "final"
    assert repeated.content_hash == hashlib.sha256(b"original").hexdigest()
    response = client.get_object(Bucket=bucket, Key=repeated.raw_object_path)
    try:
        assert response["Body"].read() == b"original"
    finally:
        response["Body"].close()
    fetcher.fetch.return_value = FetchedPage(result.url, b"changed", "text/html")
    changed = await ingest_url(session, result, fetcher=fetcher, storage=client, bucket=bucket)
    keys.append(changed.raw_object_path)
    assert changed.id != original_id and changed.source_id == source_id
    assert changed.raw_object_path != repeated.raw_object_path
    assert (
        await session.scalar(
            select(func.count()).select_from(Document).where(Document.source_id == source_id)
        )
        == 2
    )


async def test_fetch_and_storage_failures_leave_no_source(session):
    url = f"https://example.org/{uuid4()}"
    result = DiscoveryResult(url=url)
    fetcher = AsyncMock()
    fetcher.fetch.side_effect = FetchError("fetch_failed")

    class BrokenStorage:
        def put_object(self, **kwargs):
            raise OSError("storage unavailable")

    for error in (FetchError, OSError):
        with pytest.raises(error):
            await ingest_url(
                session, result, fetcher=fetcher, storage=BrokenStorage(), bucket="unused"
            )
        assert await session.scalar(select(Source.id).where(Source.canonical_url == url)) is None
        fetcher.fetch.side_effect = None
        fetcher.fetch.return_value = FetchedPage(url, b"fixture", "text/plain")


async def test_database_failure_after_upload_is_retry_safe(session, storage, monkeypatch):
    from app.db import ingestion

    client, bucket, keys = storage
    result = DiscoveryResult(url=f"https://example.org/{uuid4()}")
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchedPage(result.url, b"fixture", "text/plain")
    keys.append(
        f"raw/{hashlib.sha256(result.url.encode()).hexdigest()}/{hashlib.sha256(b'fixture').hexdigest()}"
    )
    original = ingestion.create_document
    monkeypatch.setattr(
        ingestion, "create_document", AsyncMock(side_effect=RuntimeError("db failure"))
    )
    with pytest.raises(RuntimeError):
        await ingest_url(session, result, fetcher=fetcher, storage=client, bucket=bucket)
    assert await session.scalar(select(Source.id).where(Source.canonical_url == result.url)) is None
    monkeypatch.setattr(ingestion, "create_document", original)
    document = await ingest_url(session, result, fetcher=fetcher, storage=client, bucket=bucket)
    assert document.raw_object_path == keys[0]
