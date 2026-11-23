import asyncio
import io
import socket
from unittest.mock import AsyncMock, Mock

import pytest

from app.integrations import fetch
from app.integrations.fetch import Fetcher, FetchError, canonicalize_url


@pytest.mark.parametrize(
    "url, expected",
    [
        ("HTTPS://Example.COM:443#fragment", "https://example.com/"),
        ("http://example.com:80/a?b=2&a=1", "http://example.com/a?b=2&a=1"),
        ("https://example.com/hello world", None),
        ("https://example.com/café", "https://example.com/caf%C3%A9"),
    ],
)
def test_canonicalization(url, expected):
    if expected is None:
        with pytest.raises(FetchError):
            canonicalize_url(url)
    else:
        assert canonicalize_url(url) == expected
        assert canonicalize_url(expected) == expected


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "https://u:p@example.com/",
        "https://example.com:8000/",
        "https://example.com\\@localhost/",
        "https://example.com/\r\nHost:localhost",
        "http://[fe80::1%25eth0]/",
        "https:///path",
    ],
)
def test_invalid_urls(url):
    with pytest.raises(FetchError):
        canonicalize_url(url)


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.1.1.1"],
        ["169.254.169.254"],
        ["::1"],
        ["fc00::1"],
        ["::ffff:169.254.169.254"],
        ["::ffff:127.0.0.1"],
        ["93.184.216.34", "192.168.1.1"],
        ["224.0.0.1"],
        [],
    ],
)
async def test_private_and_mixed_dns_rejected(monkeypatch, addresses):
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)) for address in addresses
            ]
        ),
    )
    with pytest.raises(FetchError, match="non_public_address"):
        await fetch.resolve_public("example.com", 80)


async def test_redirect_revalidated_and_pinned(monkeypatch):
    resolver = AsyncMock(side_effect=["93.184.216.34", FetchError("non_public_address")])
    monkeypatch.setattr(fetch, "resolve_public", resolver)
    calls = []

    def request(url, address, *_):
        calls.append((url, address))
        return 302, {"location": "http://127.0.0.1/private"}, b""

    monkeypatch.setattr(fetch, "_request", request)
    with pytest.raises(FetchError, match="insecure_redirect"):
        await Fetcher(host_interval=0).fetch("https://example.com/")
    # HTTPS downgrade is rejected before resolving or connecting to the target.
    assert len(calls) == 1 and resolver.await_count == 1


async def test_private_https_redirect_rejected(monkeypatch):
    monkeypatch.setattr(
        fetch,
        "resolve_public",
        AsyncMock(side_effect=["93.184.216.34", FetchError("non_public_address")]),
    )
    monkeypatch.setattr(
        fetch, "_request", lambda *args: (302, {"location": "https://127.0.0.1/private"}, b"")
    )
    with pytest.raises(FetchError, match="non_public_address"):
        await Fetcher(host_interval=0).fetch("https://example.com/")


async def test_retries_and_redirect_success(monkeypatch):
    monkeypatch.setattr(fetch, "resolve_public", AsyncMock(return_value="93.184.216.34"))
    replies = iter(
        [
            (503, {}, b""),
            (302, {"location": "/final"}, b""),
            (200, {"content-type": "text/html"}, b"hello"),
        ]
    )
    calls = []

    def request(url, address, *_):
        calls.append((url, address))
        return next(replies)

    monkeypatch.setattr(fetch, "_request", request)
    page = await Fetcher(host_interval=0).fetch("https://example.com/")
    assert page.url == "https://example.com/final" and page.content == b"hello"
    assert len(calls) == 3 and all(address == "93.184.216.34" for _, address in calls)


@pytest.mark.parametrize(
    "status, error, count",
    [(404, "http_status", 1), (503, "fetch_failed", 2), (302, "redirect_limit", 4)],
)
async def test_attempt_and_redirect_limits(monkeypatch, status, error, count):
    monkeypatch.setattr(fetch, "resolve_public", AsyncMock(return_value="93.184.216.34"))
    calls = []

    def request(*args):
        calls.append(args)
        return status, {"location": "/again"}, b""

    monkeypatch.setattr(fetch, "_request", request)
    with pytest.raises(FetchError, match=error):
        await Fetcher(host_interval=0).fetch("https://example.com/")
    assert len(calls) == count


async def test_total_timeout_includes_dns(monkeypatch):
    async def resolve(*args):
        await asyncio.sleep(10)

    monkeypatch.setattr(fetch, "resolve_public", resolve)
    with pytest.raises(FetchError, match="fetch_timeout"):
        await Fetcher(timeout=0.01, host_interval=0).fetch("https://example.com/")


async def test_per_host_spacing():
    client = Fetcher(host_interval=0.03)
    await client._pace("example.com")
    start = asyncio.get_running_loop().time()
    await client._pace("other.example.com")
    await client._pace("example.com")
    assert asyncio.get_running_loop().time() - start >= 0.02


class FakeSocket:
    def settimeout(self, timeout):
        pass


def test_tls_connection_uses_checked_ip_and_original_certificate_name(monkeypatch):
    raw_socket = Mock()
    connect = Mock(return_value=raw_socket)
    context = Mock()
    monkeypatch.setattr(fetch.socket, "create_connection", connect)
    monkeypatch.setattr(fetch.ssl, "create_default_context", Mock(return_value=context))
    connection = fetch._Connection("example.org", 443, "93.184.216.34", True, 5)
    connection.connect()
    connect.assert_called_once_with(("93.184.216.34", 443), timeout=5)
    context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="example.org")


@pytest.mark.parametrize(
    "headers, body, error",
    [
        ({"Content-Type": "image/png"}, b"hello", "unsupported_content_type"),
        (
            {"Content-Type": "text/html", "Content-Encoding": "gzip"},
            b"hello",
            "unsupported_encoding",
        ),
        ({"Content-Type": "text/html", "Content-Length": "100"}, b"hello", "response_too_large"),
        ({"Content-Type": "text/html"}, b"x" * 11, "response_too_large"),
        ({"Content-Type": "text/html", "Content-Length": "8"}, b"hello", "incomplete_response"),
    ],
)
def test_stream_response_limits(monkeypatch, headers, body, error):
    class Response(io.BytesIO):
        status = 200

        def getheaders(self):
            return list(headers.items())

    class Connection:
        sock = FakeSocket()

        def __init__(self, *args):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return Response(body)

        def close(self):
            pass

    monkeypatch.setattr(fetch, "_Connection", Connection)
    with pytest.raises(FetchError, match=error):
        fetch._request("https://example.com/", "93.184.216.34", 1, 10)
