import asyncio
import logging

from temporalio.client import Client

from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def connect_with_retry(address: str, namespace: str) -> Client:
    for attempt in range(1, 31):
        try:
            return await Client.connect(address, namespace=namespace)
        except Exception:
            if attempt == 30:
                raise
            logger.info("Temporal is not ready; retrying connection (%s/30)", attempt)
            await asyncio.sleep(2)
    raise RuntimeError("Temporal connection retry loop exited unexpectedly")


async def run() -> None:
    settings = get_settings()
    await connect_with_retry(settings.temporal_address, settings.temporal_namespace)
    logger.info(
        "Connected to Temporal; experiment worker registration is scheduled for Phase 5 "
        "on task queue %s",
        settings.temporal_task_queue,
    )
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run())
