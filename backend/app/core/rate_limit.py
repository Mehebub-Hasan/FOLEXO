"""Per-user sliding-window rate limiting (Phase 7 step 2).

In-memory, per-process store keyed by (rule, user id). Deliberate scope:
- The API currently runs as a single process, where this is exact.
- Multi-replica deployments MUST swap the store for Redis (shared window);
  the `RateRule`/`hit()` interface is the swap point — ADR-010.

Applied as FastAPI dependencies (not middleware) so the identity comes from
the verified JWT (get_current_user) rather than spoofable client data:

    POST /api/v1/uploads/presign          → 30 requests / minute / user
    POST /api/v1/invoices/{id}/reprocess  → 10 requests / minute / user
"""

import logging
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, status

from app.api.deps import get_current_user  # noqa: circular-free (deps → core.security only)
from app.core.security import UserPayload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateRule:
    key: str
    limit: int
    window_seconds: int


PRESIGN_RULE = RateRule(key="uploads:presign", limit=30, window_seconds=60)
REPROCESS_RULE = RateRule(key="invoices:reprocess", limit=10, window_seconds=60)

_buckets: dict[str, deque[float]] = defaultdict(deque)
_now: Callable[[], float] = time.monotonic


def reset_rate_limits() -> None:
    """Clear all windows (test isolation; process restarts are equivalent)."""
    _buckets.clear()


def hit(user_id: str, rule: RateRule) -> tuple[bool, int]:
    """Record one attempt; return (allowed, retry_after_seconds)."""
    bucket = _buckets[f"{rule.key}:{user_id}"]
    now = _now()
    window_start = now - rule.window_seconds
    while bucket and bucket[0] <= window_start:
        bucket.popleft()
    if len(bucket) >= rule.limit:
        retry_after = max(1, math.ceil(rule.window_seconds - (now - bucket[0])))
        logger.warning(
            "rate limit hit: user %s on %s (%d/%d in window)",
            user_id,
            rule.key,
            len(bucket),
            rule.limit,
        )
        return False, retry_after
    bucket.append(now)
    return True, 0


def rate_limit_dependency(rule: RateRule) -> Callable[..., Any]:
    """FastAPI dependency enforcing `rule` per authenticated user.

    Chained after get_current_user so limits key on the verified identity,
    never on client-supplied headers or IP alone.
    """

    async def dependency(
        current_user: UserPayload = Depends(get_current_user),
    ) -> None:
        allowed, retry_after = hit(str(current_user.id), rule)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please retry shortly.",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


presign_rate_limit = rate_limit_dependency(PRESIGN_RULE)
reprocess_rate_limit = rate_limit_dependency(REPROCESS_RULE)
