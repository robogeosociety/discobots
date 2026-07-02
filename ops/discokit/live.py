"""discokit.live — the fleet's own asyncio inner loop.

One process, one asyncio event loop, many recurring jobs. This is the *level-2
application loop* of the fleet-hosting plan (obsidian-automations#149): the
per-host supervisor (level 1) supervises this as ONE child process, and most of
the fleet's recurring event handling lives here. The Phase-4 gateway layer
(discord.py liveliness) attaches to this same loop later — discord.py's client
loop IS this loop.

Each Job is a plain *sync* callable run via ``asyncio.to_thread`` on its own
cadence, so a slow Influx query or sqlite read never delays the other jobs'
scheduling, and a throwing tick is caught, logged, and retried next tick — one
bad poll can't take down the loop. Sync ticks are deliberate: the existing
dashboards (httpx / influxdb-client / sqlite3) stay untouched; async-native
jobs can join once the gateway lands.

    from discokit.live import Job, run_jobs
    run_jobs([Job("ops", 30, ops_tick), Job("loop", 60, wheel_tick)])
"""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Job:
    """A recurring tick on the inner loop.

    ``tick`` is synchronous and returns a short result string (printed as the
    job's heartbeat line, mirroring the daemons' ``[tick N] edited`` output).
    """

    name: str
    interval: float  # seconds between tick *starts* (self-correcting cadence)
    tick: Callable[[], object]


async def _run_job(job: Job, stop: asyncio.Event) -> None:
    n = 0
    while not stop.is_set():
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(job.tick)
            print(f"[{job.name} · tick {n}] {result}")
        except Exception as exc:  # noqa: BLE001 — one bad poll must not kill the loop
            print(f"[{job.name} · tick {n}] FAILED: {exc}", file=sys.stderr)
        n += 1
        delay = max(0.0, job.interval - (time.monotonic() - started))
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass  # cadence elapsed — next tick


async def run(jobs: list[Job], *, stop: asyncio.Event | None = None) -> None:
    """Run all jobs concurrently until ``stop`` is set (or SIGTERM/SIGINT)."""
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, ValueError):
            pass  # non-main thread / platform without signal support (tests)
    names = ", ".join(f"{j.name}/{j.interval:g}s" for j in jobs)
    print(f"[live] inner loop up — {len(jobs)} job(s): {names}")
    await asyncio.gather(*(_run_job(j, stop) for j in jobs))
    print("[live] inner loop stopped.")


def run_jobs(jobs: list[Job]) -> None:
    """Sync entry point: host the jobs on a fresh event loop until signalled."""
    asyncio.run(run(jobs))
