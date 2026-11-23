from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
async def client():
    application = create_app(Settings(_env_file=None, environment="test"))
    application.state.db_sessions = Mock(side_effect=AssertionError("unexpected database access"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client
    application.state.db_sessions.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/v1/locations/not-a-uuid/hypotheses",
        f"/v1/locations/{uuid4()}/hypotheses?limit=21",
        f"/v1/locations/{uuid4()}/hypotheses?offset=-1",
        f"/v1/locations/{uuid4()}/hypotheses?offset=1001",
        f"/v1/locations/{uuid4()}/hypotheses?q=%20%20",
        f"/v1/locations/{uuid4()}/hypotheses?category=%20",
        f"/v1/locations/{uuid4()}/hypotheses?research=perhaps",
        f"/v1/locations/{uuid4()}/hypotheses?unknown=private-value",
        f"/v1/locations/{uuid4()}/recent?days=7",
        f"/v1/locations/{uuid4()}/summary?offset=1",
        f"/v1/hypotheses/{uuid4()}/evidence?limit=0",
        f"/v1/hypotheses/{uuid4()}/related?offset=1001",
        f"/v1/hypotheses/{uuid4()}?requested_location_id=private-value",
    ],
)
async def test_invalid_query_is_safe_and_cannot_request_research(client, path):
    response = await client.get(path, headers={"X-Request-ID": "public-query-test"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"] == "public-query-test"
    assert response.headers["X-Request-ID"] == "public-query-test"
    assert "private-value" not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"location_id": "private-value"},
        {"incident": "private-value"},
        {"incident": "x" * 2001},
        {"limit": True},
        {"limit": 21},
        {"min_similarity": -0.1},
        {"min_similarity": "0.7"},
        {"min_similarity": True},
        {"private_field": "private-value"},
    ],
)
async def test_match_validation_never_echoes_incident_or_bad_values(client, changes):
    payload = {
        "location_id": str(uuid4()),
        "incident": "A driver demanded extra money after a fixed fare.",
        **changes,
    }
    response = await client.post("/v1/hypotheses/match", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert "private-value" not in response.text and "A driver demanded" not in response.text


async def test_openapi_documents_public_models_filters_and_actual_error_envelope(client):
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    search = paths["/v1/locations/{location_id}/hypotheses"]["get"]
    assert {item["name"] for item in search["parameters"]} == {
        "location_id",
        "q",
        "category",
        "limit",
        "offset",
        "inherit_parents",
        "include_descendants",
        "research",
    }
    assert search["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/HypothesisPage"
    )
    for code in ("404", "422", "503", "500"):
        assert search["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ErrorEnvelope"
        )
    for suffix in ("/evidence", "/related", ""):
        assert f"/v1/hypotheses/{{hypothesis_id}}{suffix}" in paths
    assert "/v1/locations/{location_id}/recent" in paths
    assert "/v1/locations/{location_id}/summary" in paths
    assert "/v1/hypotheses/match" in paths and "/v1/reports" in paths
    assert paths["/v1/reports"]["post"]["responses"]["409"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/ErrorEnvelope")
    assert schema["components"]["schemas"]["IncidentMatchRequest"]["additionalProperties"] is False
    days = next(
        p
        for p in paths["/v1/locations/{location_id}/recent"]["get"]["parameters"]
        if p["name"] == "days"
    )
    assert days["schema"]["enum"] == [30, 90, 365]
