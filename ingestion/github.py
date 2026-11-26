"""Async GitHub client: conditional requests, rate-limit aware, retrying, paginating.

Read-only by design: the only verbs are GET and the GraphQL POST (which we only use for queries).
Writes to the sandbox repo live in `agent/actions.py`, behind an allow-list.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.github.com"
_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class CachedResponse:
    etag: str | None
    last_modified: str | None
    data: Any
    link: str | None


class HttpCache(Protocol):
    async def get(self, url: str) -> CachedResponse | None: ...

    async def put(self, url: str, response: CachedResponse) -> None: ...


class MemoryHttpCache:
    def __init__(self) -> None:
        self.entries: dict[str, CachedResponse] = {}

    async def get(self, url: str) -> CachedResponse | None:
        return self.entries.get(url)

    async def put(self, url: str, response: CachedResponse) -> None:
        self.entries[url] = response


@dataclass
class RequestStats:
    requests: int = 0
    not_modified: int = 0
    retries: int = 0
    rate_limit_waits: int = 0
    slept_seconds: float = 0.0


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


def next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = _LINK_NEXT.search(link_header)
    return match.group(1) if match else None


def build_url(path_or_url: str, params: Mapping[str, str | int] | None = None) -> str:
    url = path_or_url if path_or_url.startswith("http") else f"{API_URL}{path_or_url}"
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urlencode(sorted(params.items()))}"
    return url


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        *,
        cache: HttpCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.time,
        max_retries: int = 5,
        min_remaining: int = 50,
        backoff_base: float = 1.0,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "opspilot-ingestor",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(headers=headers, transport=transport, timeout=30.0)
        self._cache = cache or MemoryHttpCache()
        self._sleep = sleep
        self._clock = clock
        self._max_retries = max_retries
        self._min_remaining = min_remaining
        self._backoff_base = backoff_base
        self._remaining: int | None = None
        self._reset_at: float | None = None
        self._limit: int | None = None
        self.stats = RequestStats()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _wait(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        self.stats.slept_seconds += seconds
        await self._sleep(seconds)

    async def _respect_budget(self) -> None:
        if self._remaining is None or self._reset_at is None:
            return
        threshold = self._min_remaining
        if self._limit is not None:
            threshold = min(threshold, self._limit // 20)
        if self._remaining <= threshold and self._reset_at > self._clock():
            self.stats.rate_limit_waits += 1
            log.warning("rate limit budget low (%s left), waiting for reset", self._remaining)
            await self._wait(self._reset_at - self._clock() + 1)
            self._remaining = None

    def _track(self, response: httpx.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        reset = response.headers.get("x-ratelimit-reset")
        limit = response.headers.get("x-ratelimit-limit")
        if remaining is not None:
            self._remaining = int(remaining)
        if reset is not None:
            self._reset_at = float(reset)
        if limit is not None:
            self._limit = int(limit)

    def _limited_delay(self, response: httpx.Response) -> float | None:
        """Seconds to wait if this is a primary or secondary rate-limit response, else None."""
        if response.status_code not in (403, 429):
            return None
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            return float(retry_after)
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = float(response.headers.get("x-ratelimit-reset", self._clock() + 60))
            return reset - self._clock() + 1
        if response.status_code == 429 or "rate limit" in response.text.lower():
            return 60.0
        return None

    async def _send(
        self, method: str, url: str, *, headers: dict[str, str], json: Any = None
    ) -> httpx.Response:
        attempt = 0
        while True:
            await self._respect_budget()
            self.stats.requests += 1
            try:
                response = await self._http.request(method, url, headers=headers, json=json)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise
                log.warning("transport error on %s: %s", url, exc)
            else:
                self._track(response)
                delay = self._limited_delay(response)
                if delay is not None:
                    if attempt >= self._max_retries:
                        raise GitHubError(response.status_code, "rate limited")
                    self.stats.rate_limit_waits += 1
                    await self._wait(delay)
                    attempt += 1
                    continue
                if response.status_code not in RETRYABLE_STATUS or attempt >= self._max_retries:
                    return response
            attempt += 1
            self.stats.retries += 1
            await self._wait(min(60.0, self._backoff_base * 2 ** (attempt - 1)))

    async def get(
        self, path_or_url: str, params: Mapping[str, str | int] | None = None
    ) -> CachedResponse:
        """GET with ETag / Last-Modified revalidation. 304s do not count against the rate limit."""
        url = build_url(path_or_url, params)
        cached = await self._cache.get(url)
        headers: dict[str, str] = {}
        if cached is not None:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified
        response = await self._send("GET", url, headers=headers)
        if response.status_code == 304 and cached is not None:
            self.stats.not_modified += 1
            return cached
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        fresh = CachedResponse(
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            data=response.json(),
            link=response.headers.get("link"),
        )
        if fresh.etag or fresh.last_modified:
            await self._cache.put(url, fresh)
        return fresh

    async def paginate(
        self,
        path: str,
        params: Mapping[str, str | int] | None = None,
        *,
        max_items: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        url: str | None = build_url(path, {"per_page": 100, **(params or {})})
        seen = 0
        while url is not None:
            page = await self.get(url)
            for item in page.data:
                yield item
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            url = next_link(page.link)

    async def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        response = await self._send(
            "POST",
            f"{API_URL}/graphql",
            headers={},
            json={"query": query, "variables": dict(variables)},
        )
        if response.status_code >= 400:
            raise GitHubError(response.status_code, response.text[:200])
        payload: dict[str, Any] = response.json()
        if payload.get("errors"):
            raise GitHubError(200, str(payload["errors"])[:200])
        data: dict[str, Any] = payload["data"]
        limit = data.get("rateLimit")
        if isinstance(limit, dict) and "remaining" in limit:
            self._remaining = int(limit["remaining"])
            if limit.get("resetAt"):
                self._reset_at = datetime.fromisoformat(str(limit["resetAt"])).timestamp()
        return data
