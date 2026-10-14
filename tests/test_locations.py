import httpx
import pytest

from app.api.locations import get_session
from app.core.config import Settings
from app.db.locations import normalize_name
from app.main import create_app


@pytest.mark.parametrize(
    "value, expected",
    [
        (" Bangkok ", "bangkok"),
        ("Grand\t Palace", "grand palace"),
        ("ＢＫＫ", "bkk"),
        ("Straße", "strasse"),
        (" \n ", ""),
    ],
)
def test_location_name_normalization(value, expected):
    assert normalize_name(value) == expected


@pytest.mark.parametrize(
    "url",
    [
        "/v1/locations?q=",
        "/v1/locations?q=%20%20",
        "/v1/locations?q=BKK&limit=101",
        "/v1/locations?q=BKK&offset=-1",
        "/v1/locations/resolve?q=BKK&country_code=invalid",
        "/v1/locations/not-a-uuid",
    ],
)
async def test_invalid_lookup_inputs_fail_without_database(url):
    application = create_app(Settings(_env_file=None))

    async def no_database():
        yield None

    application.dependency_overrides[get_session] = no_database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get(url)
    assert response.status_code == 422
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
