# core/retry.py
# Exponential backoff with jitter for all external calls.
# Wrap every HTTP call and every LLM call with with_retry().
import asyncio
import logging
from typing import Callable, TypeVar, Any

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Status codes that are safe to retry (transient)
DEFAULT_RETRYABLE_CODES = (429, 500, 502, 503, 504)


async def with_retry(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    retryable_codes: tuple = DEFAULT_RETRYABLE_CODES,
    **kwargs,
) -> Any:
    """
    Call fn(*args, **kwargs) with exponential backoff.
    - Retries on rate limits (429) and transient server errors (5xx).
    - Never retries on 400, 401, 403, 404 — those are your fault, not the API's.
    - Raises the last exception if all attempts fail.
    """
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)

        except Exception as e:
            # Try to extract HTTP status code from exception
            response = getattr(e, "response", None)
            status = getattr(response, "status_code", None)

            # Non-retryable HTTP errors — fail immediately
            if status and status not in retryable_codes:
                logger.error(
                    f"Non-retryable error {status} calling {fn.__name__}: {e}"
                )
                raise

            last_error = e

            if attempt == max_attempts:
                break

            # Exponential backoff with 20% jitter
            delay = base_delay * (2 ** (attempt - 1))
            jitter = delay * 0.2 * (asyncio.get_event_loop().time() % 1)
            wait = delay + jitter

            logger.warning(
                f"Attempt {attempt}/{max_attempts} failed for {fn.__name__}: {e}. "
                f"Retrying in {wait:.1f}s..."
            )
            await asyncio.sleep(wait)

    raise RuntimeError(
        f"All {max_attempts} attempts failed for {fn.__name__}. "
        f"Last error: {last_error}"
    )
