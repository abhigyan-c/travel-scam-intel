import base64
import hashlib
from uuid import uuid4

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError
from test_scheduling import NOW, candidate

from app.core.config import Settings
from app.main import create_app
from app.scheduling import SchedulerPolicy, priority
from app.schemas.admin import AdminCommand


@pytest.mark.parametrize(
    "path",
    [
        "/v1/admin/overview",
        "/v1/admin/items/reports",
        "/v1/admin/items/jobs",
        "/v1/admin/audit",
        "/v1/admin/commands",
    ],
)
async def test_admin_reads_and_writes_fail_closed(path):
    application = create_app(Settings(_env_file=None, environment="test", admin_api_token="x" * 40))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await (
            client.post(path, json={}) if path.endswith("/commands") else client.get(path)
        )
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["www-authenticate"] == "Bearer"


async def test_dashboard_is_empty_self_contained_login_with_exact_csp_hashes():
    application = create_app(Settings(_env_file=None, environment="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/admin")
        assert response.status_code == 200
        html = response.text
        for tag, start, end in [
            ("script", '<script type="module">', "</script>"),
            ("style", "<style>", "</style>"),
        ]:
            body = html.split(start, 1)[1].split(end, 1)[0]
            digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            assert f"{tag}-src 'sha256-{digest}'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "localStorage" not in html and "sessionStorage" not in html
        assert '<section id="private" hidden>' in html
        assert "/* EXPLORER_" not in html
        assert (await client.get("/v1/admin/overview")).status_code == 503


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed": False},
        {"confirmed": "true"},
        {"reason": " "},
        {"expected_updated_at": "2026-11-22T00:00:00"},
        {"extra": "no"},
    ],
)
def test_admin_commands_require_explicit_confirmation_reason_and_aware_cas(changes):
    body = {
        "operation": "set_status",
        "subject_id": str(uuid4()),
        "expected_updated_at": "2026-11-22T00:00:00Z",
        "confirmed": True,
        "reason": "Reviewed",
        "status": "candidate",
    }
    with pytest.raises(ValidationError):
        TypeAdapter(AdminCommand).validate_python({**body, **changes})


def test_location_priority_changes_rank_but_never_eligibility():
    policy = SchedulerPolicy()
    base = priority(candidate(), policy, NOW)["priority"]
    assert priority(candidate(priority_adjustment=100), policy, NOW)["priority"] == base + 100
    assert priority(candidate(priority_adjustment=-100), policy, NOW)["priority"] == base - 100
    for changes in [
        {"busy": True},
        {"next_retry": NOW.replace(year=2031)},
        {"last_run": NOW},
        {"d30": 0, "last_demand": None, "last_run": None},
    ]:
        assert priority(candidate(priority_adjustment=100, **changes), policy, NOW) is None
