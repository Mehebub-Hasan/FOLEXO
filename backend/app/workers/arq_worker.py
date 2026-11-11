"""ARQ worker entrypoint.

Run locally: `arq app.workers.arq_worker.WorkerSettings`
"""

import logging
import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from arq.connections import RedisSettings

from app.database.session import SessionLocal, dispose_engine
from app.workers.tasks import process_invoice_task

# Ensure .env is loaded
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)

# Parse Redis connection URL cleanly with SSL support
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
parsed = urlparse(redis_url)

is_ssl = parsed.scheme == "rediss"
redis_settings = RedisSettings(
    host=parsed.hostname or "localhost",
    port=parsed.port or 6379,
    password=parsed.password,
    ssl=is_ssl,
)


async def startup(ctx: dict) -> None:
    logger.info("Initializing worker session pool...")
    ctx["session_factory"] = SessionLocal


async def shutdown(ctx: dict) -> None:
    logger.info("Disposing worker database engine...")
    await dispose_engine()


class WorkerSettings:
    redis_settings = redis_settings
    functions = [process_invoice_task]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    poll_delay = 0.5