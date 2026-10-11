import json
import logging
from io import StringIO

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def application():
    application = create_app(Settings(_env_file=None, name="Test intelligence", environment="test"))
    application.state.log_output = StringIO()
    logging.getLogger("app").handlers[0].setStream(application.state.log_output)
    return application


@pytest.fixture
async def client(application):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


async def test_identity_and_health(client):
    assert (await client.get("/")).json() == {"name": "Test intelligence", "version": "0.1.0"}
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


async def test_not_found_uses_error_contract(client):
    response = await client.get("/missing", headers={"X-Request-ID": "missing-request"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "request_id": "missing-request",
        }
    }
    assert response.headers["X-Request-ID"] == "missing-request"


async def test_http_error_preserves_status_and_headers(application, client):
    @application.get("/limited")
    async def limited():
        raise HTTPException(429, "Try again later", headers={"Retry-After": "30"})

    response = await client.get("/limited")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


async def test_validation_error_does_not_reflect_private_input(application, client):
    class Input(BaseModel):
        count: int

    @application.post("/validate")
    async def validate(body: Input):
        return body

    response = await client.post("/validate", json={"count": "private-submission"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"] == [{"field": ["body", "count"], "type": "int_parsing"}]
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert "private-submission" not in response.text


async def test_unexpected_error_is_safe_and_correlated(application, client):
    @application.get("/broken")
    async def broken():
        raise RuntimeError("private-token-value")

    response = await client.get("/broken", headers={"X-Request-ID": "failed-request"})
    healthy = await client.get("/health")
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred",
            "request_id": "failed-request",
        }
    }
    assert response.headers["X-Request-ID"] == "failed-request"
    assert healthy.status_code == 200
    assert healthy.headers["X-Request-ID"] != "failed-request"
    logs = application.state.log_output.getvalue()
    assert "private-token-value" not in response.text + logs
    records = [json.loads(line) for line in logs.splitlines()]
    failed = [record for record in records if record["request_id"] == "failed-request"]
    assert [record["message"] for record in failed] == ["request_failed", "request_completed"]
    assert failed[0]["error_type"] == "RuntimeError"
    assert failed[1]["status"] == 500


async def test_openapi_documents_health(client):
    schema = (await client.get("/openapi.json")).json()
    assert schema["info"]["title"] == "Test intelligence"
    assert "/health" in schema["paths"]
