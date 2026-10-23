import hashlib
import os
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.documents import create_document, find_exact_duplicates
from app.db.processing import process_document
from app.integrations.storage import create_storage_client, ensure_bucket
from app.models import Source
from app.models.document import DocumentStatus
from app.models.source import SourceType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]

TEXT = (
    "The visitors reported that a driver asked them to pay an additional fare after their journey. "
    "They had agreed on the price before they left the railway station. " * 4
)


@pytest.fixture
def storage():
    client = create_storage_client(Settings())
    bucket = Settings().s3_bucket
    ensure_bucket(client, bucket)
    keys = []
    yield client, bucket, keys
    try:
        for key in keys:
            client.delete_object(Bucket=bucket, Key=key)
    finally:
        client.close()


async def make_document(session, storage, raw):
    client, bucket, keys = storage
    source = Source(platform="fixture", source_type=SourceType.NEWS)
    session.add(source)
    await session.flush()
    key = f"raw/processing/{uuid4()}"
    keys.append(key)
    client.put_object(Bucket=bucket, Key=key, Body=raw, ContentType="text/plain")
    return await create_document(
        session,
        source_id=source.id,
        raw_object_path=key,
        content_hash=hashlib.sha256(raw).hexdigest(),
        title="Acquired title",
        document_metadata={"retained": "value"},
    )


async def test_processing_preserves_raw_and_acquisition_and_is_repeatable(session, storage):
    document = await make_document(session, storage, TEXT.encode())
    acquired = document.retrieved_at, document.content_hash, document.raw_object_path
    processed = await process_document(session, document.id, storage=storage[0], bucket=storage[1])
    assert processed.processing_status == DocumentStatus.PROCESSED
    assert processed.document_metadata["processing"]["eligible_for_extraction"] is True
    assert processed.clean_text_hash == hashlib.sha256(processed.clean_text.encode()).hexdigest()
    assert (
        processed.title == "Acquired title" and processed.document_metadata["retained"] == "value"
    )
    first_hash = processed.clean_text_hash
    processed = await process_document(session, document.id, storage=storage[0], bucket=storage[1])
    assert processed.clean_text_hash == first_hash
    assert (processed.retrieved_at, processed.content_hash, processed.raw_object_path) == acquired


async def test_raw_and_clean_duplicates_keep_separate_sources(session, storage):
    first = await make_document(session, storage, TEXT.encode())
    second = await make_document(session, storage, TEXT.encode())
    third = await make_document(session, storage, ("  " + TEXT + "\n").encode())
    assert {row.id for row in await find_exact_duplicates(session, first.id)} == {second.id}
    for document in (first, second, third):
        await process_document(session, document.id, storage=storage[0], bucket=storage[1])
    assert {row.id for row in await find_exact_duplicates(session, first.id)} == {
        second.id,
        third.id,
    }
    assert first.source_id != second.source_id
    assert await find_exact_duplicates(session, uuid4()) == []


async def test_storage_and_hash_failures_are_not_empty_success(session, storage):
    document = await make_document(session, storage, TEXT.encode())
    storage[0].put_object(
        Bucket=storage[1], Key=document.raw_object_path, Body=b"corrupted", ContentType="text/plain"
    )
    processed = await process_document(session, document.id, storage=storage[0], bucket=storage[1])
    assert processed.processing_status == DocumentStatus.FAILED
    assert processed.document_metadata["processing"]["outcome"] == "raw_hash_mismatch"
    storage[0].delete_object(Bucket=storage[1], Key=document.raw_object_path)
    processed = await process_document(session, document.id, storage=storage[0], bucket=storage[1])
    assert processed.document_metadata["processing"]["outcome"] == "storage_error"
    assert processed.clean_text is None and processed.clean_text_hash is None
    assert await process_document(session, uuid4(), storage=storage[0], bucket=storage[1]) is None
