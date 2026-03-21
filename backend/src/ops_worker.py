"""Standalone background worker for inventory and syslog sync loops.

Run with:

    cd backend
    python -m src.ops_worker
"""

from __future__ import annotations

import asyncio

from src.ops.db import init_db
from src.ops.runtime import OpsEmbeddedScheduler


async def _main() -> None:
    init_db()
    scheduler = OpsEmbeddedScheduler()
    await scheduler.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await scheduler.stop()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
