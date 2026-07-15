"""HTTP retry/backoff helper for rate-limited APIs.

Ported from the reference Go tool's http_retry.go. Retries transient failures
(429/408/5xx status codes, connection errors) with exponential backoff, honoring
the `Retry-After` header on 429 responses instead of guessing a wait time.
"""

from __future__ import annotations

import logging
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 408, 502, 503}


class HTTPRetryExhaustedError(Exception):
    """Raised when every retry attempt hit a retryable status with no usable response."""


class ExponentialBackoff:
    def __init__(
        self,
        initial_interval: float = 1.0,
        max_interval: float = 30.0,
        multiplier: float = 2.0,
    ) -> None:
        self.initial_interval = initial_interval
        self.max_interval = max_interval
        self.multiplier = multiplier

    def duration(self, attempt: int) -> float:
        if attempt <= 0:
            return 0.0
        delay = self.initial_interval * (self.multiplier ** (attempt - 1))
        return min(delay, self.max_interval)


def _should_retry_status(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS_CODES or 500 <= status_code < 600


def _is_retryable_exception(exc: requests.RequestException) -> bool:
    # Only retry failures a second attempt could plausibly fix. Anything else
    # (bad URL, SSL errors, invalid request) will just fail the same way again.
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


def _parse_retry_after(value: str) -> float | None:
    """Parse a Retry-After header per RFC 7231 7.1.3: delay-seconds or an HTTP-date."""
    value = value.strip()
    if value.isdigit():
        seconds = int(value)
        return float(seconds) if seconds > 0 else None
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    remaining = (target - datetime.now(timezone.utc)).total_seconds()
    return remaining if remaining > 0 else None


def _retry_after_or_backoff(
    response: requests.Response | None, attempt: int, backoff: ExponentialBackoff
) -> float:
    if response is not None and response.status_code == 429:
        header = response.headers.get("Retry-After")
        if header:
            parsed = _parse_retry_after(header)
            if parsed is not None:
                return parsed
    return backoff.duration(attempt)


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    backoff: ExponentialBackoff | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Perform one HTTP request, retrying transient failures.

    On persistent retryable status codes (rate limited, 5xx) with no exception,
    raises HTTPRetryExhaustedError once retries run out rather than handing back
    a degraded response for the caller to misinterpret as success. On persistent
    connection errors, the original exception propagates instead.
    """
    backoff = backoff or ExponentialBackoff()
    last_exc: requests.RequestException | None = None
    wait_seconds = 0.0

    for attempt in range(max_retries + 1):
        if attempt > 0:
            logger.warning(
                "[HTTP RETRY] attempt %d/%d for %s %s (waiting %.1fs)",
                attempt, max_retries, method, url, wait_seconds,
            )
            time.sleep(wait_seconds)

        try:
            response = session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries and _is_retryable_exception(exc):
                wait_seconds = backoff.duration(attempt + 1)
                continue
            raise

        if not _should_retry_status(response.status_code):
            return response

        if attempt < max_retries:
            wait_seconds = _retry_after_or_backoff(response, attempt + 1, backoff)
        response.close()

    if last_exc is not None:
        raise last_exc
    raise HTTPRetryExhaustedError(f"max retries ({max_retries}) exhausted for {method} {url}")


class RetryableSession:
    """A requests.Session wrapper that retries transient failures. See
    request_with_retry() for the exact retry/backoff behavior."""

    def __init__(
        self,
        *,
        max_retries: int = 3,
        backoff: ExponentialBackoff | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.backoff = backoff or ExponentialBackoff()
        self.session = session or requests.Session()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        return request_with_retry(
            self.session, method, url, max_retries=self.max_retries, backoff=self.backoff, **kwargs
        )

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("PATCH", url, **kwargs)
