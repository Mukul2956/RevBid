"""Time-bounded shutdown steps.

A graceful shutdown must never be able to hang: the orchestrator will SIGKILL us after a grace
period anyway, and a stuck step (e.g. a socket whose close never completes) would otherwise
delay every deploy until then. Each step gets a budget; if it overruns we log, abandon it and
carry on with the rest of the teardown.

Deliberately uses ``asyncio.wait`` rather than ``wait_for``: ``wait_for`` waits for the
cancelled task to finish, which is exactly what hangs when a task ignores cancellation.
"""

import asyncio
from collections.abc import Awaitable
from typing import Any

import structlog

log = structlog.stdlib.get_logger("app.shutdown")


async def run_bounded(awaitable: Awaitable[Any], *, budget_s: float, step: str) -> None:
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=budget_s)
    if not done:
        task.cancel()
        log.warning("shutdown_step_timed_out", step=step, budget_s=budget_s)
        return
    if not task.cancelled() and task.exception() is not None:
        log.warning("shutdown_step_failed", step=step, error=repr(task.exception()))
