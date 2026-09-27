import functools
import logging
import time
from typing import Callable, TypeVar

import httpx

T = TypeVar("T")

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (httpx.TransportError, httpx.HTTPStatusError)


def retry_on_transient_error(retries: int = 2, base_delay: float = 1.5) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retries a function on network/5xx errors with linear backoff. 4xx errors
    (bad request, auth, not found) are not retried since retrying won't help."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last_exc: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    return fn(*args, **kwargs)
                except httpx.HTTPStatusError as exc:
                    if exc.response is not None and exc.response.status_code < 500:
                        raise  # client error - retrying won't help
                    last_exc = exc
                except httpx.TransportError as exc:
                    last_exc = exc
                if attempt < retries:
                    delay = base_delay * (attempt + 1)
                    logger.warning("%s failed (attempt %d/%d), retrying in %.1fs: %s",
                                    fn.__name__, attempt + 1, retries + 1, delay, last_exc)
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
