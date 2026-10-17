from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.document import Document, DocumentStatus


async def get_document(session: AsyncSession, document_id: UUID) -> Document | None:
    return await session.scalar(
        select(Document)
        .where(Document.id == document_id)
        .options(joinedload(Document.source))
        .execution_options(populate_existing=True)
    )


async def create_document(
    session: AsyncSession,
    *,
    source_id: UUID,
    raw_object_path: str,
    content_hash: str,
    url: str | None = None,
    title: str | None = None,
    published_at: datetime | None = None,
    retrieved_at: datetime | None = None,
    document_metadata: dict | None = None,
) -> Document:
    values = {
        "source_id": source_id,
        "raw_object_path": raw_object_path,
        "content_hash": content_hash,
        "url": url,
        "title": title,
        "published_at": published_at,
        "document_metadata": document_metadata if document_metadata is not None else {},
    }
    if retrieved_at is not None:
        values["retrieved_at"] = retrieved_at
    document_id = await session.scalar(
        insert(Document)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_document_version")
        .returning(Document.id)
    )
    if document_id is None:
        document_id = await session.scalar(
            select(Document.id).where(
                Document.source_id == source_id, Document.content_hash == content_hash
            )
        )
    return await get_document(session, document_id)


async def list_documents(
    session: AsyncSession, source_id: UUID, *, limit: int = 20, offset: int = 0
) -> list[Document]:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1–100 and offset must be non-negative")
    documents = await session.scalars(
        select(Document)
        .where(Document.source_id == source_id)
        .options(joinedload(Document.source))
        .order_by(Document.retrieved_at.desc(), Document.created_at.desc(), Document.id)
        .limit(limit)
        .offset(offset)
    )
    return list(documents)


async def set_document_processing(
    session: AsyncSession,
    document_id: UUID,
    *,
    processing_status: DocumentStatus,
    clean_text: str | None,
    language: str | None,
    document_metadata: dict,
) -> Document | None:
    updated_id = await session.scalar(
        update(Document)
        .where(Document.id == document_id)
        .values(
            processing_status=processing_status,
            clean_text=clean_text,
            language=language,
            document_metadata=Document.document_metadata.op("||")(document_metadata),
        )
        .returning(Document.id)
    )
    return await get_document(session, updated_id) if updated_id is not None else None
