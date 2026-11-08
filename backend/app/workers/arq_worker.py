"""ARQ worker entrypoint.

Run locally: `arq app.workers.arq_worker.WorkerSettings`
(docs/ARCHITECTURE.md §5 — Redis + ARQ, ADR-002).
"""

import logging

from arq.connections import RedisSettings

from app.core.config import settings
from app.database.session import SessionLocal, dispose_engine
from app.workers.tasks import process_invoice_task

logger = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    """Initialize the DB connection pool once per worker process."""
    ctx["session_factory"] = SessionLocal
    logging.basicConfig(level=settings.log_level)
    logger.info("worker starting (redis=%s, env=%s)", settings.redis_url, settings.app_env)


async def shutdown(ctx: dict) -> None:
    await dispose_engine()
    logger.info("worker shut down")


class WorkerSettings:
    functions = [process_invoice_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # docs/ARCHITECTURE.md §5.2
    job_timeout = 300   # whole-job cap, seconds
    max_tries = 3       # total attempts including the first → 2 retries
    retry_delay = 10    # seconds between attempts
    max_jobs = 10
    health_check_interval = 60
