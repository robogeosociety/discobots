# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "fakeredis"]
# ///
"""Unit tests for discokit.bus — the fleet message bus (against fakeredis).

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest --with fakeredis python -m pytest discokit/tests/test_bus.py
"""

from __future__ import annotations

import pytest

from discokit.bus import Bus


@pytest.fixture
def bus():
    fakeredis = pytest.importorskip("fakeredis")
    return Bus(src="test", client=fakeredis.FakeRedis(decode_responses=True))


# --- degradability: the whole point --------------------------------------------
def test_disabled_bus_is_a_safe_noop():
    dead = Bus(url=None)  # no URL ⇒ never connects
    assert dead.enabled is False
    assert dead.publish("t", {"a": 1}) is False
    assert dead.retained("t") is None
    assert dead.emit("s", {"a": 1}) is None
    assert dead.read_group("s", "g", "c") == []
    dead.ack("s", "g", "1-0")  # must not raise


# --- telemetry: publish + retained last-value ----------------------------------
def test_publish_sets_a_retained_envelope(bus):
    assert bus.publish("fleet.supervisor.tick", {"ok": True, "lag_s": 0.2}) is True
    env = bus.retained("fleet.supervisor.tick")
    assert env["v"] == 1 and env["src"] == "test"
    assert env["topic"] == "fleet.supervisor.tick"
    assert env["data"] == {"ok": True, "lag_s": 0.2}
    assert isinstance(env["ts"], float)


def test_retained_missing_topic_is_none(bus):
    assert bus.retained("never.published") is None


def test_retained_ttl_is_applied(bus):
    bus.publish("t", {"x": 1}, ttl=123)
    assert 0 < bus.client.ttl("retain:t") <= 123


# --- events: durable stream + consumer group -----------------------------------
def test_emit_and_read_group_roundtrip(bus):
    bus.emit("fleet.discord.reaction", {"emoji": "👍", "msg": "m1"})
    bus.emit("fleet.discord.reaction", {"emoji": "🔥", "msg": "m2"})
    got = bus.read_group("fleet.discord.reaction", "trainer", "c1")
    assert [e["data"]["emoji"] for _id, e in got] == ["👍", "🔥"]


def test_consumer_group_does_not_redeliver_after_ack(bus):
    bus.emit("s", {"n": 1})
    first = bus.read_group("s", "g", "c1")
    assert len(first) == 1
    bus.ack("s", "g", first[0][0])
    # a fresh read for the same group sees no new (unacked) messages
    assert bus.read_group("s", "g", "c1") == []


def test_stream_replay_for_a_new_group(bus):
    bus.emit("s", {"n": 1})
    bus.read_group("s", "groupA", "c")  # groupA consumes it
    # a brand-new group starts at 0 and still sees the history
    assert len(bus.read_group("s", "groupB", "c")) == 1


# --- coordination: distributed lock -------------------------------------------
def test_lock_acquire_then_contend(bus):
    tok = bus.lock_acquire("job", ttl=30)
    assert tok is not None
    assert bus.lock_acquire("job", ttl=30) is None
    bus.lock_release("job", tok)
    assert bus.lock_acquire("job", ttl=30) is not None


def test_lock_release_only_if_owner(bus):
    tok = bus.lock_acquire("job", ttl=30)
    bus.lock_release("job", "not-my-token")
    assert bus.lock_acquire("job", ttl=30) is None
    bus.lock_release("job", tok)
    assert bus.lock_acquire("job", ttl=30) is not None


def test_locked_context_manager(bus):
    with bus.locked("panel") as got:
        assert got is True
        with bus.locked("panel") as got2:
            assert got2 is False
    with bus.locked("panel") as got3:
        assert got3 is True


def test_disabled_bus_lock_fails_open():
    dead = Bus(url=None)
    tok = dead.lock_acquire("job")
    assert tok is not None
    dead.lock_release("job", tok)
    with dead.locked("job") as got:
        assert got is True


def test_incr_counts_and_sets_ttl(bus):
    assert bus.incr("posts", window=60) == 1
    assert bus.incr("posts", window=60) == 2
    assert bus.incr("posts", window=60, amount=5) == 7
    assert 0 < bus.client.ttl("count:posts") <= 60


def test_incr_disabled_bus_is_none():
    assert Bus(url=None).incr("posts") is None
