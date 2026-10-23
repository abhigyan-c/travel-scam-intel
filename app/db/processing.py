import asyncio
import hashlib
from uuid import UUID

from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.documents import get_document, set_document_processing
from app.models.document import Document, DocumentStatus
from app.processing import PROCESSING_VERSION, ProcessedText, process_bytes


def _read_raw(storage: BaseClient, bucket: str, key: str):
    response = storage.get_object(Bucket=bucket, Key=key)
    try:
        raw = response["Body"].read(2_000_001)
        return raw, response.get("ContentType", "")
    finally:
        response["Body"].close()


async def process_document(
    session: AsyncSession, document_id: UUID, *, storage: BaseClient, bucket: str
) -> Document | None:
    document = await get_document(session, document_id)
    if document is None:
        return None
    try:
        raw, content_type = await asyncio.to_thread(
            _read_raw, storage, bucket, document.raw_object_path
        )
        if hashlib.sha256(raw).hexdigest() != document.content_hash:
            result = ProcessedText(None, None, "raw_hash_mismatch", {})
        else:
            result = await asyncio.to_thread(
                process_bytes, raw, content_type=content_type, url=document.url
            )
    except (BotoCoreError, ClientError, OSError):
        result = ProcessedText(None, None, "storage_error", {})
    successful = result.outcome in {"ready", "non_english", "unknown_language"}
    return await set_document_processing(
        session,
        document_id,
        processing_status=DocumentStatus.PROCESSED if successful else DocumentStatus.FAILED,
        clean_text=result.text,
        language=result.language,
        document_metadata={
            "processing": {
                "version": PROCESSING_VERSION,
                "outcome": result.outcome,
                "eligible_for_extraction": result.outcome == "ready",
                **result.metadata,
            }
        },
    )
