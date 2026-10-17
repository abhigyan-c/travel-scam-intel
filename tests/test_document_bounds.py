from uuid import uuid4

import pytest

from app.db.documents import list_documents


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (20, -1)])
async def test_document_listing_rejects_invalid_bounds(limit, offset):
    with pytest.raises(ValueError):
        await list_documents(None, uuid4(), limit=limit, offset=offset)
