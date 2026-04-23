"""Retry utilities for transient API failures."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


async def with_retry(
    fn: Callable[[], Awaitable[object]],
    max_retries: int = 3,
    base_delay: float = 1.0,
):
    """Retry `fn` on transient HTTP errors with exponential backoff + jitter.

    Retries only `httpx.HTTPStatusError` when status is 429 or >=500.
    """
    attempt = 0
    while True:
        try:
            return await fn()
        except httpx.HTTPStatusError as e:
            status = getattr(e.response, "status_code", None)
            if status is None or not _is_retryable_status(int(status)):
                raise
            if attempt >= max_retries:
                raise

            # Exponential backoff with jitter.
            delay = base_delay * (2**attempt) * random.uniform(0.5, 1.5)
            attempt += 1
            await asyncio.sleep(delay)

