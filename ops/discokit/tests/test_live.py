# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.live — the asyncio inner loop.

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_live.py
"""

from __future__ import annotations

import asyncio

from discokit.live import Job, run


def _run_for(jobs: list[Job], seconds: float) -> None:
    """Host the jobs, set the stop event after `seconds`, wait for shutdown."""

    async def harness() -> None:
        stop = asyncio.Event()

        async def stopper() -> None:
            await asyncio.sleep(seconds)
            stop.set()

        await asyncio.gather(run(jobs, stop=stop), stopper())

    asyncio.run(harness())


def test_each_job_ticks_on_its_own_cadence():
    counts = {"fast": 0, "slow": 0}

    def make_tick(name):
        def tick():
            counts[name] += 1
            return "ok"

        return tick

    _run_for([Job("fast", 0.05, make_tick("fast")), Job("slow", 10, make_tick("slow"))], 0.3)
    assert counts["fast"] >= 3  # several ticks inside the window
    assert counts["slow"] == 1  # first tick fires immediately; next is 10s away


def test_a_throwing_tick_does_not_kill_the_loop(capsys):
    counts = {"good": 0, "bad": 0}

    def good():
        counts["good"] += 1
        return "ok"

    def bad():
        counts["bad"] += 1
        raise RuntimeError("boom")

    _run_for([Job("good", 0.05, good), Job("bad", 0.05, bad)], 0.3)
    assert counts["bad"] >= 2  # kept being retried after raising
    assert counts["good"] >= 3  # and never disturbed the healthy job
    assert "FAILED: boom" in capsys.readouterr().err


def test_a_slow_tick_does_not_delay_the_other_jobs():
    counts = {"fast": 0}

    def slow():
        import time

        time.sleep(0.2)  # blocks its worker thread, not the loop
        return "ok"

    def fast():
        counts["fast"] += 1
        return "ok"

    _run_for([Job("slow", 10, slow), Job("fast", 0.05, fast)], 0.3)
    assert counts["fast"] >= 3


def test_stop_interrupts_the_intertick_sleep_promptly():
    ticks = {"n": 0}

    def tick():
        ticks["n"] += 1
        return "ok"

    async def harness() -> float:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        start = loop.time()

        async def stopper() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(run([Job("j", 3600, tick)], stop=stop), stopper())
        return loop.time() - start

    elapsed = asyncio.run(harness())
    assert ticks["n"] == 1
    assert elapsed < 1.0  # did not wait out the hour-long cadence
