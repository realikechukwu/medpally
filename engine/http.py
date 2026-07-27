"""Rate-limited, retrying HTTP transport for E-utilities.

The original scripts used a flat ``time.sleep(0.34)`` between batches, which is
simultaneously too slow when an API key is present (10 req/s is allowed) and too
fragile when it is absent (no backoff at all on a 429). This replaces both with a
token bucket plus exponential backoff.

Requests are sent as POST. E-utilities supports it, and it removes the URL-length
ceiling entirely — which matters because a journal OR-list grows with every
journal a user subscribes to.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Mapping

import requests

from .errors import RateLimitError, TransportError

logger = logging.getLogger(__name__)

# NCBI's documented ceilings: 3 requests/second anonymously, 10 with an API key.
# We sit just under each to leave headroom for clock skew.
RATE_WITH_KEY = 9.0
RATE_WITHOUT_KEY = 2.5

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class TokenBucket:
    """Thread-safe token bucket. ``acquire()`` blocks until a token is free."""

    def __init__(self, rate_per_second: float, capacity: float | None = None) -> None:
        self.rate = rate_per_second
        self.capacity = capacity if capacity is not None else max(1.0, rate_per_second)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = (1.0 - self._tokens) / self.rate
            time.sleep(deficit)


class HttpClient:
    """POST-capable HTTP client with a shared rate limiter and retries."""

    def __init__(
        self,
        *,
        rate_per_second: float,
        user_agent: str,
        timeout: float = 30.0,
        max_attempts: int = 5,
        session: requests.Session | None = None,
    ) -> None:
        self.bucket = TokenBucket(rate_per_second)
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent

    def post(self, url: str, data: Mapping[str, str]) -> bytes:
        last_status: int | None = None

        for attempt in range(1, self.max_attempts + 1):
            self.bucket.acquire()
            try:
                response = self.session.post(url, data=dict(data), timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.max_attempts:
                    raise TransportError(f"{url} failed after {attempt} attempts: {exc}") from exc
                self._sleep_backoff(attempt, reason=str(exc))
                continue

            if response.status_code in RETRY_STATUSES:
                last_status = response.status_code
                if attempt == self.max_attempts:
                    break
                self._sleep_backoff(
                    attempt,
                    reason=f"HTTP {response.status_code}",
                    retry_after=response.headers.get("Retry-After"),
                )
                continue

            if response.status_code >= 400:
                raise TransportError(f"{url} returned HTTP {response.status_code}")

            return response.content

        if last_status == 429:
            raise RateLimitError(f"{url} rate-limited after {self.max_attempts} attempts")
        raise TransportError(
            f"{url} returned HTTP {last_status} after {self.max_attempts} attempts"
        )

    def _sleep_backoff(self, attempt: int, *, reason: str, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 2.0**attempt
        else:
            # Full jitter: spreads retries out when several workers back off
            # together. Not security-sensitive, so `random` is fine here.
            delay = random.uniform(0, min(2.0**attempt, 30.0))
        logger.warning("retrying in %.1fs (attempt %d): %s", delay, attempt, reason)
        time.sleep(delay)
