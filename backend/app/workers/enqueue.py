"""Redis/ARQ enqueue helper with graceful degradation.

Contract (Phase 4): Redis unavailability must never fail an upload. When the
broker is unreachable the invoice simply stays `uploaded`, a warning is
logged, and the row is picked up later by a sweeper or manual reprocess.
The pool is cached per process; API tests override the
`get_enqueue_process_invoice` dependency to avoid needing Redis at all.
"""

import logging
from collections.abc import Awaitable, Callable

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings
from app.workers.tasks import PROCESS_INVOICE_JOB

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def close_arq_pool() -> None:
    """Release the cached pool (app shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_process_invoice(invoice_id: str) -> bool:
    """Enqueue the extraction job. Returns False when Redis is unavailable."""
    try:
        pool = await _get_pool()
        job = await pool.enqueue_job(PROCESS_INVOICE_JOB, invoice_id=invoice_id)
        return job is not None
    except Exception as exc:  # noqa: BLE001 — deliberate: any broker failure degrades to UPLOADED
        logger.warning(
            "Redis unavailable — invoice %s stays UPLOADED and will need a manual "
            "reprocess or sweeper pick-up (%s)",
            invoice_id,
            exc,
        )
        return False


def get_enqueue_process_invoice() -> Callable[[str], Awaitable[bool]]:
    """FastAPI dependency so tests can substitute the broker interaction."""
    return enqueue_process_invoice
