import functools
import logging
import time
from typing import Callable, TypeVar

import httpx

T = TypeVar("T")

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (httpx.TransportError, httpx.HTTPStatusError)


def _is_retryable_status(status_code: int) -> bool:
    # 5xx: transient server-side failure. 429: rate limited - exactly the case
    # retry-with-backoff exists for, not a "won't help to retry" client error like a
    # 404 or 401. Every other 4xx is a genuine client error retrying can't fix.
    return status_code >= 500 or status_code == 429


def retry_on_transient_error(retries: int = 2, base_delay: float = 1.5) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retries a function on network/5xx/429 errors with linear backoff (honoring a
    Retry-After header when the server sends one, e.g. for rate limiting). Other 4xx
    errors (bad request, auth, not found) are not retried since retrying won't help."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    return fn(*args, **kwargs)
                except httpx.HTTPStatusError as exc:
                    if exc.response is None or not _is_retryable_status(exc.response.status_code):
                        raise  # client error - retrying won't help
                    last_exc = exc
                    delay = _retry_after_seconds(exc.response) or base_delay * (attempt + 1)
                except httpx.TransportError as exc:
                    last_exc = exc
                    delay = base_delay * (attempt + 1)
                if attempt < retries:
                    logger.warning("%s failed (attempt %d/%d), retrying in %.1fs: %s",
                                    fn.__name__, attempt + 1, retries + 1, delay, last_exc)
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # Retry-After can also be an HTTP-date, which we don't bother parsing
