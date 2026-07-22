"""discokit.daemon — the watchdog tick loop for the edit-in-place dashboards.

Every dashboard bot is the same shape: do a thing, sleep, repeat, and never let a
transient blip kill the loop. `serve()` is that loop — with one addition that the
standalone bots lacked and that cost us a **3-day silent wedge**:

    a per-tick SIGALRM deadline.

The standalone loops already caught exceptions and set socket `timeout=`, yet one
still hung for three days. The reason: a socket timeout does **not** bound DNS
resolution — `getaddrinfo` can block indefinitely — so when name resolution wedged
after a host sleep, the tick blocked forever *inside* the timeout-protected call,
below the `except`. The process stayed alive (so Docker's restart-on-exit never
fired) but stopped ticking.

`serve()` arms an interval timer before each tick and disarms it after; if a tick
outlasts `deadline`, SIGALRM raises `TickTimeout`, the loop logs it and moves on.
A hang now self-heals on the next tick instead of hanging until someone notices.

    serve(tick, interval=60, label="discord-mini-mem")

`tick` is called with no args; raise anything to signal failure — it's logged and
the loop continues. Runs in the main thread (SIGALRM requires it), which the
single-purpose dashboard containers always do.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager


class TickTimeout(Exception):
    """A single tick outran its watchdog deadline (likely a wedged syscall)."""


@contextmanager
def _deadline(seconds: float):
    """Raise TickTimeout if the wrapped block runs longer than `seconds`."""

    def _fire(signum, frame):
        raise TickTimeout(f"tick exceeded {seconds:g}s watchdog")

    prev = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prev)


def watchdog_seconds(interval: int) -> float:
    """The per-tick deadline: WATCHDOG_S env if set, else max(interval, 60)s.

    Generous on purpose — comfortably above a healthy tick (a couple of network
    round-trips well under the 15–20 s socket timeouts) so it never false-trips,
    but finite, so an *indefinite* hang is bounded to one interval, not forever.
    """
    env = os.environ.get("WATCHDOG_S")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return float(max(interval, 60))


def serve(
    tick: Callable[[], None],
    *,
    interval: int,
    label: str,
    once: bool = False,
    deadline: float | None = None,
) -> None:
    """Call `tick()` every `interval`s, each bounded by a SIGALRM watchdog.

    A raising tick (including a watchdog TickTimeout) is logged to stderr and the
    loop continues. `once=True` runs a single tick and returns (for --once smoke
    tests). Blocks forever otherwise.
    """
    if deadline is None:
        deadline = watchdog_seconds(interval)
    while True:
        try:
            with _deadline(deadline):
                tick()
        except Exception as e:  # noqa: BLE001 — a blip (or a wedge) must not kill the loop
            sys.stderr.write(f"{label}: update failed: {e}\n")
            sys.stderr.flush()
        if once:
            return
        time.sleep(interval)
