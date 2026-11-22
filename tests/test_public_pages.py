import base64
import hashlib
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
async def client():
    application = create_app(Settings(_env_file=None, environment="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize("path", ["/about", "/support", "/privacy", "/terms"])
async def test_public_page_security_and_head(client, path):
    response = await client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Travel Scam Intelligence" in response.text
    assert "<script" not in response.text.lower()
    style = response.text.split("<style>", 1)[1].split("</style>", 1)[0]
    digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
    policy = response.headers["content-security-policy"]
    assert f"style-src 'sha256-{digest}'" in policy
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert (await client.head(path)).content == b""
    assert (await client.post(path)).status_code == 405


async def test_policy_disclosures_and_no_directory_serving(client):
    privacy = (await client.get("/privacy")).text
    assert "indefinite" in privacy.lower()
    assert "abhigyanchakraborty61@gmail.com" in (await client.get("/support")).text
    for path in ["/public/privacy.html", "/public/.env", "/about/../.env"]:
        assert (await client.get(path)).status_code == 404
    assert (await client.get("/")).json()["version"] == "0.1.0"


def test_proxy_limits_new_routes_to_exact_read_only_paths():
    config = Path("deploy/Caddyfile").read_text()
    product = config.split("@product {", 1)[1].split("}", 1)[0]
    assert "method GET HEAD" in product
    assert "path /about /privacy /terms /support" in product
    verification = config.split("@verification {", 1)[1].split("}", 1)[0]
    assert "method GET HEAD" in verification
    assert "path /.well-known/openai-apps-challenge" in verification
    assert "respond 404" in config
