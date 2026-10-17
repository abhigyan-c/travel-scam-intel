import asyncio
import hashlib
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.documents import create_document, get_document, list_documents, set_document_processing
from app.integrations.storage import create_storage_client, ensure_bucket
from app.models.document import Document, DocumentStatus
from app.models.source import Source, SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
async def source(session):
    source = Source(
        platform="example.org",
        canonical_url=f"https://example.org/reports/{uuid4()}",
        source_type=SourceType.NEWS,
        author_identifier="fixture-author",
        source_metadata={"publication": "fixture"},
    )
    session.add(source)
    await session.flush()
    return source


async def test_document_recovers_source_after_reload(session, source):
    published = datetime(2026, 1, 1, tzinfo=UTC)
    retrieved = datetime(2026, 1, 2, tzinfo=UTC)
    source_id = source.id
    document = await create_document(
        session,
        source_id=source_id,
        url=source.canonical_url,
        title="Source fixture",
        raw_object_path="raw/fixture.html",
        content_hash=content_hash("raw content"),
        published_at=published,
        retrieved_at=retrieved,
        document_metadata={"content_type": "text/html"},
    )
    identity = document.id
    session.expunge_all()
    stored = await get_document(session, identity)
    assert stored.source.id == source_id
    assert stored.source.author_identifier == "fixture-author"
    assert stored.source.source_metadata == {"publication": "fixture"}
    assert stored.url == stored.source.canonical_url
    assert stored.published_at == published and stored.retrieved_at == retrieved
    assert stored.processing_status == DocumentStatus.PENDING
    assert stored.clean_text is None and stored.language is None
    assert stored.created_at.tzinfo is not None and stored.updated_at.tzinfo is not None
    assert stored.document_metadata == {"content_type": "text/html"}


async def test_reingestion_reuses_version_without_overwriting_acquisition(session, source):
    original = await create_document(
        session,
        source_id=source.id,
        raw_object_path="raw/original.html",
        content_hash=content_hash("same"),
        title="Original",
        document_metadata={"fetch": "first"},
    )
    identity, acquired_at = original.id, original.retrieved_at
    await set_document_processing(
        session,
        identity,
        processing_status=DocumentStatus.PROCESSED,
        clean_text="Derived content",
        language="en",
        document_metadata={"parser": "v1"},
    )
    repeated = await create_document(
        session,
        source_id=source.id,
        raw_object_path="raw/retry.html",
        content_hash=content_hash("same"),
        title="Retry",
        document_metadata={"fetch": "second"},
    )
    assert repeated.id == identity
    assert repeated.raw_object_path == "raw/original.html" and repeated.title == "Original"
    assert repeated.retrieved_at == acquired_at
    assert repeated.clean_text == "Derived content" and repeated.language == "en"
    assert repeated.processing_status == DocumentStatus.PROCESSED
    assert repeated.document_metadata == {"fetch": "first", "parser": "v1"}
    assert (
        await session.scalar(
            select(func.count()).select_from(Document).where(Document.source_id == source.id)
        )
        == 1
    )


async def test_changed_content_creates_versions_and_shared_content_keeps_each_source(
    session, source
):
    old = await create_document(
        session,
        source_id=source.id,
        raw_object_path="raw/old.html",
        content_hash=content_hash("old"),
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = await create_document(
        session,
        source_id=source.id,
        raw_object_path="raw/new.html",
        content_hash=content_hash("new"),
        retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert old.id != newer.id
    assert [row.id for row in await list_documents(session, source.id)] == [newer.id, old.id]
    assert [row.id for row in await list_documents(session, source.id, limit=1, offset=1)] == [
        old.id
    ]
    other = Source(platform="forum", source_type=SourceType.FORUM)
    session.add(other)
    await session.flush()
    shared = await create_document(
        session,
        source_id=other.id,
        raw_object_path="raw/new.html",
        content_hash=content_hash("new"),
    )
    assert shared.id != newer.id and shared.source.id == other.id


async def test_processing_changes_only_derived_fields_and_can_clear_them(session, source):
    original = await create_document(
        session,
        source_id=source.id,
        url=source.canonical_url,
        title="Original",
        raw_object_path="raw/original.html",
        content_hash=content_hash("original"),
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    acquired = (
        original.source_id,
        original.url,
        original.title,
        original.raw_object_path,
        original.content_hash,
        original.published_at,
        original.retrieved_at,
        original.created_at,
    )
    for status, clean_text, language in (
        (DocumentStatus.PROCESSED, "Cleaned", "en"),
        (DocumentStatus.FAILED, None, None),
    ):
        updated = await set_document_processing(
            session,
            original.id,
            processing_status=status,
            clean_text=clean_text,
            language=language,
            document_metadata={"processing_version": "v1"},
        )
        assert (
            updated.source_id,
            updated.url,
            updated.title,
            updated.raw_object_path,
            updated.content_hash,
            updated.published_at,
            updated.retrieved_at,
            updated.created_at,
        ) == acquired
        assert (updated.processing_status, updated.clean_text, updated.language) == (
            status,
            clean_text,
            language,
        )
    assert await get_document(session, uuid4()) is None
    assert await list_documents(session, uuid4()) == []
    assert (
        await set_document_processing(
            session,
            uuid4(),
            processing_status=DocumentStatus.FAILED,
            clean_text=None,
            language=None,
            document_metadata={},
        )
        is None
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"source_id": uuid4()},
        {"source_id": None},
        {"raw_object_path": " "},
        {"content_hash": "z" * 64},
        {"content_hash": "a" * 63},
        {"url": " "},
    ],
)
async def test_invalid_document_acquisition_is_rejected(session, source, invalid):
    values = {
        "source_id": source.id,
        "raw_object_path": "raw/fixture",
        "content_hash": content_hash(str(uuid4())),
    }
    values.update(invalid)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await create_document(session, **values)


async def test_source_cannot_be_deleted_while_a_document_depends_on_it(session, source):
    document = await create_document(
        session,
        source_id=source.id,
        raw_object_path="raw/retained",
        content_hash=content_hash("keep"),
    )
    identity = document.id
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(Source).where(Source.id == source.id))
    session.expunge_all()
    assert (await get_document(session, identity)).source.platform == "example.org"


async def test_database_rejects_duplicate_versions_and_invalid_processing(session, source):
    document = await create_document(
        session,
        source_id=source.id,
        raw_object_path="raw/version",
        content_hash=content_hash("unique"),
    )
    for invalid in (
        {"raw_object_path": "raw/duplicate", "content_hash": content_hash("unique")},
        {
            "raw_object_path": "raw/new",
            "content_hash": content_hash("bad status"),
            "processing_status": "unknown",
        },
        {
            "raw_object_path": "raw/new",
            "content_hash": content_hash("bad language"),
            "language": " ",
        },
        {
            "raw_object_path": "raw/new",
            "content_hash": content_hash("bad metadata"),
            "document_metadata": [],
        },
    ):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(Document(source_id=source.id, **invalid))
                await session.flush()
    assert (await get_document(session, document.id)).source.id == source.id


async def test_user_report_document_does_not_require_public_url(session):
    source = Source(platform="traveler", source_type=SourceType.USER_REPORT)
    session.add(source)
    await session.flush()
    document = await create_document(
        session,
        source_id=source.id,
        raw_object_path="reports/fixture.json",
        content_hash=content_hash("report"),
    )
    assert document.url is None and document.source.canonical_url is None
    assert document.source.source_type == SourceType.USER_REPORT


async def test_raw_object_is_recoverable_through_document_and_source(session, source):
    settings = Settings()
    storage = create_storage_client(settings)
    key = f"integration/documents/{uuid4()}.html"
    body = b"<p>Raw source fixture</p>"
    try:
        await asyncio.to_thread(ensure_bucket, storage, settings.s3_bucket)
        await asyncio.to_thread(storage.put_object, Bucket=settings.s3_bucket, Key=key, Body=body)
        document = await create_document(
            session,
            source_id=source.id,
            raw_object_path=key,
            content_hash=hashlib.sha256(body).hexdigest(),
            url=source.canonical_url,
        )
        identity = document.id
        session.expunge_all()
        loaded = await get_document(session, identity)
        assert loaded.source.canonical_url == loaded.url
        response = await asyncio.to_thread(
            storage.get_object, Bucket=settings.s3_bucket, Key=loaded.raw_object_path
        )
        try:
            recovered = await asyncio.to_thread(response["Body"].read)
        finally:
            response["Body"].close()
        assert recovered == body and hashlib.sha256(recovered).hexdigest() == loaded.content_hash
    finally:
        try:
            await asyncio.to_thread(storage.delete_object, Bucket=settings.s3_bucket, Key=key)
        finally:
            storage.close()
