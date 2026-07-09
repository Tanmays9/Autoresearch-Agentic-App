from __future__ import annotations

import asyncio
import signal

from .database import SessionLocal, init_db
from .services.bootstrap import bootstrap_queued_runs
from .services.orchestration import release_expired_leases
from .services.crawler import process_crawl_jobs


async def main() -> None:
    init_db()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass
    while not stop.is_set():
        with SessionLocal() as db:
            release_expired_leases(db)
            await bootstrap_queued_runs(db)
            await process_crawl_jobs(db)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            continue


if __name__ == "__main__":
    asyncio.run(main())
