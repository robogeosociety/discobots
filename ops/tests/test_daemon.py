"""Tests for discokit.daemon.serve — the per-tick watchdog loop.

The regression this guards: a tick that hangs (as a wedged DNS lookup did for 3
days) must be bounded by the SIGALRM deadline and self-heal, not block forever.
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from discokit import daemon  # noqa: E402


def test_once_runs_a_single_tick_and_returns():
    calls = []
    daemon.serve(lambda: calls.append(1), interval=999, label="t", once=True)
    assert calls == [1]


def test_raising_tick_is_logged_not_propagated(capsys):
    def boom():
        raise RuntimeError("kaboom")

    daemon.serve(boom, interval=999, label="unit", once=True)   # must not raise
    err = capsys.readouterr().err
    assert "unit: update failed" in err and "kaboom" in err


def test_watchdog_bounds_a_hung_tick(capsys):
    """A tick that would block far longer than the deadline is interrupted."""
    def hang():
        time.sleep(30)          # simulates a wedged syscall (e.g. DNS getaddrinfo)

    start = time.monotonic()
    daemon.serve(hang, interval=999, label="wd", once=True, deadline=0.3)
    elapsed = time.monotonic() - start
    assert elapsed < 5          # interrupted at ~0.3s, nowhere near 30
    err = capsys.readouterr().err
    assert "wd: update failed" in err and "watchdog" in err


def test_watchdog_seconds_prefers_env(monkeypatch):
    monkeypatch.setenv("WATCHDOG_S", "12.5")
    assert daemon.watchdog_seconds(60) == 12.5
    monkeypatch.delenv("WATCHDOG_S")
    assert daemon.watchdog_seconds(30) == 60.0     # floor
    assert daemon.watchdog_seconds(90) == 90.0     # >= interval


def test_deadline_restores_prior_alarm_handler():
    import signal
    sentinel = signal.getsignal(signal.SIGALRM)
    daemon.serve(lambda: None, interval=999, label="t", once=True, deadline=1)
    assert signal.getsignal(signal.SIGALRM) is sentinel
