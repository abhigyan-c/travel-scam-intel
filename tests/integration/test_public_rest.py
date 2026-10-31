import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.main import create_app
from app.models import HypothesisRelation, Location, LocationResearchState, ResearchJob
from app.services.intelligence import IntelligenceService
from tests.integration.test_intelligence_services import SUMMARY, seed
from tests.integration.test_on_demand_research import environment as environment

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INFRA_TESTS") != "1", reason="set RUN_INFRA_TESTS=1"),
]


@pytest.fixture
async def api(environment):
    ctx, location, _ = environment
    application = create_app(ctx["settings"])
    application.state.db_sessions = ctx["sessions"]
    application.state.report_queue = ctx["redis"]
    application.state.embedder = ctx["embedder"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client, ctx, location


async def test_search_filters_pagination_and_actual_geography(api):
    client, ctx, country = api
    async with ctx["sessions"]() as session, session.begin():
        city = Location(
            name="Nadi", canonical_name="nadi", type="city", country_code="FJ", parent_id=country
        )
        sibling = Location(
            name="Suva", canonical_name="suva", type="city", country_code="FJ", parent_id=country
        )
        session.add_all([city, sibling])
        await session.flush()
    broad = await seed(ctx, country)
    local = await seed(ctx, city.id)
    await seed(ctx, sibling.id)
    await seed(ctx, city.id, data={"sensitive": True})
    url = f"/v1/locations/{city.id}/hypotheses"
    response = await client.get(
        url, params={"research": "false", "limit": 1, "q": "driver", "category": "transport"}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 2 and result["has_more"] and result["items"][0]["id"] == str(local[0])
    assert result["items"][0]["scope"] == "exact"
    assert result["research"]["status"] == "never_researched"
    assert response.headers["Cache-Control"] == "no-store"
    second = (await client.get(url, params={"research": "false", "limit": 1, "offset": 1})).json()
    assert second["items"][0]["id"] == str(broad[0]) and second["items"][0]["scope"] == "ancestor"
    assert second["items"][0]["location"]["id"] == str(country) and not second["has_more"]
    narrow = (
        await client.get(url, params={"research": "false", "inherit_parents": "false"})
    ).json()
    assert narrow["total"] == 1
    assert (await client.get(url, params={"research": "false", "q": "%"})).json()["total"] == 0
    assert "PRIVATE INTERNAL" not in response.text
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 0


async def test_hypothesis_evidence_related_and_private_not_found(api):
    client, ctx, location = api
    public = await seed(ctx, location)
    report = await seed(ctx, location, hypothesis_id=public[0], report=True)
    held = await seed(ctx, location, data={"sensitive": True})
    related = await seed(ctx, location)
    async with ctx["sessions"]() as session, session.begin():
        session.add_all(
            [
                HypothesisRelation(
                    source_hypothesis_id=public[0],
                    target_hypothesis_id=related[0],
                    relation_type="SUBTYPE_OF",
                ),
                HypothesisRelation(
                    source_hypothesis_id=public[0],
                    target_hypothesis_id=held[0],
                    relation_type="RELATED_TO",
                ),
            ]
        )
    detail = await client.get(f"/v1/hypotheses/{public[0]}")
    assert detail.status_code == 200 and detail.json()["description"] == SUMMARY
    evidence = await client.get(f"/v1/hypotheses/{public[0]}/evidence")
    assert evidence.status_code == 200 and evidence.json()["total"] == 2
    anonymous = next(item for item in evidence.json()["items"] if item["id"] == str(report[2]))
    assert (
        anonymous["label"] == "anonymous_traveler_report"
        and anonymous["provenance_class"] == "SECOND_HAND"
    )
    assert anonymous["excerpt"] is None and anonymous["source_url"] is None
    for private in (
        "PRIVATE",
        "private-author",
        "private/",
        *(str(i) for i in report[1:] if i != report[2]),
    ):
        assert private not in evidence.text
    relation = (await client.get(f"/v1/hypotheses/{public[0]}/related")).json()
    assert relation["total"] == 1 and relation["items"][0]["direction"] == "outgoing"
    for identity in (held[0], uuid4()):
        for suffix in ("", "/evidence", "/related"):
            response = await client.get(f"/v1/hypotheses/{identity}{suffix}")
            assert (
                response.status_code == 404
                and response.json()["error"]["message"] == "Hypothesis not found"
            )
            assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


async def test_recent_patterns_and_summary_do_not_substitute_publication_dates(api):
    client, ctx, location = api
    recent = await seed(ctx, location, occurred_at=datetime.now(UTC) - timedelta(days=2))
    await seed(ctx, location, occurred_at=None)
    await seed(ctx, location, occurred_at=datetime.now(UTC) - timedelta(days=100))
    response = await client.get(
        f"/v1/locations/{location}/recent", params={"research": "false", "days": 30}
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [str(recent[0])]
    summary = (await client.get(f"/v1/locations/{location}/summary?research=false")).json()
    assert summary["total_patterns"] == 3 and summary["exact_patterns"] == 3
    assert summary["categories"] == {"transport": 3} and summary["outcome"] == "findings"


async def test_empty_incomplete_and_research_requests_share_one_durable_job(api):
    client, ctx, location = api
    url = f"/v1/locations/{location}/hypotheses"
    untouched = (await client.get(url + "?research=false")).json()
    assert (
        untouched["outcome"] == "research_incomplete"
        and untouched["research"]["status"] == "never_researched"
    )
    responses = await asyncio.gather(client.get(url), client.get(url))
    for response in responses:
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["outcome"] == "research_incomplete" and result["research"]["in_progress"]
        assert result["items"] == []
        for internal in ("active_job_id", "run_token", "lease", "progress", "error_code"):
            assert internal not in result["research"]
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 1
    ctx["provider"].search.assert_not_called()
    ctx["llm"].complete.assert_not_called()


async def test_fresh_empty_and_stale_results_and_queue_outage(api):
    client, ctx, location = api
    now = datetime.now(UTC)
    async with ctx["sessions"]() as session, session.begin():
        session.add(
            LocationResearchState(
                location_id=location,
                status="ready",
                initial_completed_at=now,
                last_success_at=now,
                fresh_until=now + timedelta(hours=1),
                progress={"complete": True, "private-progress": "secret"},
            )
        )
    fresh = await client.get(f"/v1/locations/{location}/summary")
    assert fresh.status_code == 200 and fresh.json()["outcome"] == "nothing_found"
    ctx["redis"].enqueue_job.assert_not_called()
    await seed(ctx, location)
    async with ctx["sessions"]() as session, session.begin():
        (await session.get(LocationResearchState, location)).fresh_until = now - timedelta(hours=1)
    ctx["redis"].enqueue_job.side_effect = ConnectionError("private Redis connection string")
    refresh = await client.get(f"/v1/locations/{location}/hypotheses")
    assert refresh.status_code == 200 and refresh.json()["total"] == 1
    assert refresh.json()["research"]["in_progress"] and refresh.json()["research"]["stale"]
    assert "secret" not in refresh.text and "Redis" not in refresh.text
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 1


async def test_incident_match_is_read_only_and_model_failure_is_not_no_match(api):
    client, ctx, location = api
    public = await seed(ctx, location)
    ctx["embedder"].embed.side_effect = lambda texts: [[1.0] + [0.0] * 383 for _ in texts]
    payload = {
        "location_id": str(location),
        "incident": "A driver demanded more money after the agreed fare. "
        "Call me at person@example.org.",
    }
    matched = await client.post("/v1/hypotheses/match", json=payload)
    assert matched.status_code == 200, matched.text
    assert matched.json()["status"] == "matched" and matched.json()["items"][0]["hypothesis"][
        "id"
    ] == str(public[0])
    assert "person@example.org" not in str(ctx["embedder"].embed.call_args) + matched.text
    assert matched.json()["limitations"] == ["input_redacted"]
    ctx["embedder"].preload.side_effect = OSError("private model path")
    failed = await client.post("/v1/hypotheses/match", json=payload)
    assert failed.status_code == 200 and failed.json()["status"] == "unavailable"
    assert "private model path" not in failed.text
    async with ctx["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ResearchJob)) == 0


async def test_missing_location_and_database_failure_use_safe_error_contract(api, monkeypatch):
    client, _, location = api
    for suffix in ("hypotheses", "summary", "recent"):
        missing = await client.get(f"/v1/locations/{uuid4()}/{suffix}")
        assert missing.status_code == 404, missing.text
        assert missing.json()["error"]["message"] == "Location not found"
    monkeypatch.setattr(
        IntelligenceService,
        "search_location_scams",
        AsyncMock(
            side_effect=OperationalError(
                "SELECT secret", {}, RuntimeError("private DB credentials")
            )
        ),
    )
    failed = await client.get(f"/v1/locations/{location}/hypotheses?research=false")
    assert failed.status_code == 503, failed.text
    assert failed.json()["error"]["request_id"] == failed.headers["X-Request-ID"]
    assert "secret" not in failed.text and "credentials" not in failed.text
