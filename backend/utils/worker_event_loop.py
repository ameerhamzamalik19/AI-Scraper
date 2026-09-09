import asyncio
import logging
import threading

from websocket_manager import set_main_loop

logger = logging.getLogger(__name__)


def start_worker_event_loop(worker_name: str) -> None:
    """Provide a loop for synchronous worker threads to publish progress."""
    loop = asyncio.new_event_loop()

    def run_loop() -> None:
        asyncio.set_event_loop(loop)
        set_main_loop(loop)
        logger.info("Publisher event loop started for %s: %s", worker_name, id(loop))
        loop.run_forever()

    threading.Thread(
        target=run_loop,
        daemon=True,
        name=f"{worker_name}-redis-publisher",
    ).start()
