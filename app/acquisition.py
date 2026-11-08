"""Deterministic source diversity and run-local failure feedback, not a reputation score."""

import asyncio
from collections import Counter, defaultdict, deque
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.integrations.fetch import FetchError

POLICY_VERSION = "acquisition-v1"
TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "msclkid",
}


def dedup_key(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.casefold() not in TRACKING_KEYS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def host_key(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def diversify(results):
    """Preserve rank within a host, but try distinct hosts before further same-host pages."""
    queues = defaultdict(deque)
    for result in results:
        queues[host_key(result.url)].append(result)
    ordered = []
    while queues:
        for host in list(queues):
            ordered.append(queues[host].popleft())
            if not queues[host]:
                del queues[host]
    return ordered


class AcquisitionPolicy:
    def __init__(self):
        self.blocked = {}
        self.transient, self.empty, self.denied = Counter(), Counter(), Counter()
        self.diagnostics = {"version": POLICY_VERSION, "host_attempts": {}, "deferred": {}}

    def allow(self, url):
        host = host_key(url)
        reason = self.blocked.get(host)
        if reason:
            counts = self.diagnostics["deferred"]
            counts[reason] = counts.get(reason, 0) + 1
            return False
        attempts = self.diagnostics["host_attempts"]
        attempts[host] = attempts.get(host, 0) + 1
        return True

    def permit_redirect(self, url):
        reason = self.blocked.get(host_key(url))
        if reason:
            counts = self.diagnostics["deferred"]
            counts[reason] = counts.get(reason, 0) + 1
        return reason is None

    def failure(self, url, error):
        # A failed redirected host does not condemn the origin's other pages.
        host = error.host.casefold().removeprefix("www.") if error.host else host_key(url)
        if error.status == 429:
            self.blocked[host] = "rate_limited"
        elif error.status in {401, 403}:
            # One private/paywalled page is not evidence that every page is blocked.
            self.denied[host] += 1
            if self.denied[host] >= 2:
                self.blocked[host] = "repeated_access_denied"
        elif str(error) in {"fetch_failed", "fetch_timeout", "acquisition_timeout"}:
            self.transient[host] += 1
            if self.transient[host] >= 2:
                self.blocked[host] = "repeated_transport_failure"

    def acquired(self, url):
        self.transient[host_key(url)] = 0
        self.denied[host_key(url)] = 0

    def processed(self, url, outcome):
        host = host_key(url)
        if outcome == "empty":
            self.empty[host] += 1
            if self.empty[host] >= 2:
                self.blocked[host] = "repeated_empty_content"
        elif outcome in {"ready", "non_english", "unknown_language"}:
            self.empty[host] = 0


class CandidateFetcher:
    def __init__(self, delegate, timeout, policy=None):
        self.delegate, self.timeout, self.policy = delegate, timeout, policy

    async def fetch(self, url):
        try:
            async with asyncio.timeout(self.timeout):
                kwargs = {}
                if self.policy and getattr(self.delegate, "supports_host_guard", False) is True:
                    kwargs["host_allowed"] = self.policy.permit_redirect
                page = await self.delegate.fetch(url, **kwargs)
                if self.policy:
                    self.policy.acquired(page.url)
                return page
        except TimeoutError:
            raise FetchError("acquisition_timeout", host=urlsplit(url).hostname) from None
