import asyncio
import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit, urlunsplit


class FetchError(ValueError):
    def __init__(self, code: str, *, status: int | None = None, host: str | None = None):
        super().__init__(code)
        self.status, self.host = status, host


FETCH_ERROR_CODES = frozenset(
    {
        "invalid_url",
        "non_public_address",
        "unsupported_encoding",
        "unsupported_content_type",
        "response_too_large",
        "incomplete_response",
        "redirect_limit_or_missing_location",
        "insecure_redirect",
        "rate_limited",
        "http_status",
        "fetch_failed",
        "fetch_timeout",
        "acquisition_timeout",
        "host_deferred",
    }
)


def canonicalize_url(value: str) -> str:
    if len(value) > 4096 or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise FetchError("invalid_url")
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError
        if parts.username is not None or parts.password is not None or "\\" in value:
            raise ValueError
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parts.port
        if port not in {None, 80 if parts.scheme.lower() == "http" else 443}:
            raise ValueError
        if "%" in host:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit(
            (
                parts.scheme.lower(),
                authority,
                quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                quote(parts.query, safe="/%?:@!$&'()*+,;=-._~"),
                "",
            )
        )
    except (ValueError, UnicodeError) as exc:
        raise FetchError("invalid_url") from exc


@dataclass(frozen=True)
class FetchedPage:
    url: str
    content: bytes
    content_type: str


async def resolve_public(host: str, port: int) -> str:
    addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(
        not ipaddress.ip_address(row[4][0]).is_global
        or ipaddress.ip_address(row[4][0]).is_multicast
        for row in addresses
    ):
        raise FetchError("non_public_address")
    return addresses[0][4][0]


class _Connection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, tls: bool, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self.address, self.tls = address, tls

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), timeout=self.timeout)
        if self.tls:
            self.sock = ssl.create_default_context().wrap_socket(
                self.sock, server_hostname=self.host
            )


def _request(url: str, address: str, timeout: float, max_bytes: int):
    parts = urlsplit(url)
    connection = _Connection(
        parts.hostname,
        443 if parts.scheme == "https" else 80,
        address,
        parts.scheme == "https",
        timeout,
    )
    deadline = time.monotonic() + timeout
    try:
        connection.request(
            "GET",
            urlunsplit(("", "", parts.path, parts.query, "")),
            headers={
                "User-Agent": "TravelScamIntel/0.1",
                "Accept-Encoding": "identity",
                "Accept": "text/html, text/plain, application/xhtml+xml",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status != 200:
            return response.status, headers, b""
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise FetchError("unsupported_encoding")
        media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
            raise FetchError("unsupported_content_type")
        if "content-length" in headers and int(headers["content-length"]) > max_bytes:
            raise FetchError("response_too_large")
        content = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > max_bytes:
                raise FetchError("response_too_large")
        if "content-length" in headers and len(content) != int(headers["content-length"]):
            raise FetchError("incomplete_response")
        return response.status, headers, bytes(content)
    finally:
        connection.close()


class Fetcher:
    supports_host_guard = True

    def __init__(
        self,
        *,
        timeout: float = 25,
        request_timeout: float = 5,
        max_bytes: int = 2_000_000,
        attempts: int = 2,
        redirects: int = 3,
        host_interval: float = 0.5,
    ):
        if timeout <= 0 or request_timeout <= 0 or max_bytes < 1 or not 1 <= attempts <= 3:
            raise ValueError("invalid fetch limits")
        if not 0 <= redirects <= 5 or host_interval < 0:
            raise ValueError("invalid fetch limits")
        self.timeout, self.request_timeout, self.max_bytes = timeout, request_timeout, max_bytes
        self.attempts, self.redirects, self.host_interval = attempts, redirects, host_interval
        self._next_host: dict[str, float] = {}

    async def _pace(self, host: str):
        now = asyncio.get_running_loop().time()
        scheduled = max(now, self._next_host.get(host, now))
        self._next_host[host] = scheduled + self.host_interval
        await asyncio.sleep(scheduled - now)

    async def fetch(self, url: str, *, host_allowed=None) -> FetchedPage:
        try:
            async with asyncio.timeout(self.timeout):
                for attempt in range(self.attempts):
                    current = canonicalize_url(url)
                    try:
                        for hop in range(self.redirects + 1):
                            parts = urlsplit(current)
                            if host_allowed is not None and not host_allowed(current):
                                raise FetchError("host_deferred", host=parts.hostname)
                            await self._pace(parts.hostname)
                            address = await resolve_public(
                                parts.hostname, 443 if parts.scheme == "https" else 80
                            )
                            status, headers, content = await asyncio.to_thread(
                                _request, current, address, self.request_timeout, self.max_bytes
                            )
                            if status in {301, 302, 303, 307, 308}:
                                if hop == self.redirects or not headers.get("location"):
                                    raise FetchError("redirect_limit_or_missing_location")
                                target = canonicalize_url(urljoin(current, headers["location"]))
                                if parts.scheme == "https" and urlsplit(target).scheme != "https":
                                    raise FetchError("insecure_redirect")
                                current = target
                                continue
                            if status == 429:
                                # Do not retry a rate-limited origin immediately or evade it.
                                raise FetchError("rate_limited", status=status, host=parts.hostname)
                            if status in {500, 502, 503, 504}:
                                raise OSError("transient_http_status")
                            if status != 200:
                                raise FetchError("http_status", status=status, host=parts.hostname)
                            return FetchedPage(current, content, headers["content-type"])
                    except (OSError, http.client.HTTPException):
                        if attempt + 1 == self.attempts:
                            raise FetchError("fetch_failed", host=parts.hostname) from None
                        await asyncio.sleep(0.25 * (attempt + 1))
        except TimeoutError:
            raise FetchError("fetch_timeout", host=parts.hostname) from None
        raise FetchError("fetch_failed")
