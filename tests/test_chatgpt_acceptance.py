from uuid import UUID

from benchmarks.chatgpt_acceptance import LOCAL_DB, LOCATION, DelayedReport, settings


async def test_host_fixture_finishes_after_five_minutes_and_joins_one_root():
    now = 0
    fixture = DelayedReport(object(), clock=lambda: now)
    first = await fixture.request({}, LOCATION, watch_seconds=600)
    second = await fixture.request({}, LOCATION, watch_seconds=600)
    assert first["run_id"] == second["run_id"]
    assert first["watch"]["id"] != second["watch"]["id"]
    assert first["latest_run"]["status"] == "queued"
    now = 10
    assert (await fixture.get(fixture.run))["latest_run"]["status"] == "researching"
    now = 299
    assert not (await fixture.get(fixture.run))["latest_run"]["terminal"]
    now = 300
    final = await fixture.wait(
        fixture.run, UUID(first["watch"]["id"]), first["revision"], wait_seconds=0
    )
    assert final["outcome"] == "nothing_found"
    assert final["latest_run"]["terminal"]
    assert final["items"] == []
    assert final["wait_status"] == "terminal"


async def test_host_fixture_waits_have_a_hard_bound_and_no_provider_settings():
    fixture = DelayedReport(object())
    result = await fixture.request({}, LOCATION, watch_seconds=600)
    watch = UUID(result["watch"]["id"])
    for _ in range(60):
        await fixture.wait(fixture.run, watch, result["revision"], wait_seconds=0)
    assert (await fixture.wait(fixture.run, watch, result["revision"], wait_seconds=0))[
        "wait_status"
    ] == "limit_reached"
    assert settings.database_url.get_secret_value() == LOCAL_DB
    assert "@127.0.0.1:" in LOCAL_DB
    assert settings.serper_api_key.get_secret_value() == ""
    assert settings.sarvam_api_key.get_secret_value() == ""
    assert settings.s3_bucket == "private-memory-only"
