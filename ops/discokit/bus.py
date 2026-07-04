"""discokit.bus — the fleet message bus (Valkey/Redis), degradable by design.

The seam between the fleet's separate loops — the obsidian-automations
supervisor, discobot-live, tommybot, and the future gateway. It exists to let
fault-isolated loops coordinate WITHOUT calling into each other: every publish
is fire-and-forget, every consumer reads independently, and a bus outage
degrades to each consumer's direct-poll fallback and NEVER blocks a loop tick.
The bus is an accelerant, not a dependency. Full contract: docs/BUS.md.

Two delivery classes, because there are two kinds of traffic:
    telemetry   publish(topic, data)  → PUBLISH fan-out + a retained last-value
                (SET … EX) so a late subscriber renders immediately; drop-safe.
                retained(topic)       → the last envelope, or None.
    events      emit(stream, data)    → XADD to a capped stream (durable).
                read_group(...)/ack() → consumer-group reads: at-least-once,
                replay, per-consumer offsets. For feedback/commands.

redis-py is imported lazily and every call is wrapped: with no reachable bus
(no BUS_URL, or the server is down) publish/emit return falsy and
retained/read_group return None/[] — so importing discokit never needs the
dependency and a --dry path needs no server.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time

RETAIN_PREFIX = "retain:"
STREAM_PREFIX = "stream:"
DEFAULT_RETAIN_TTL = 300  # seconds a retained telemetry value survives without a refresh


class Bus:
    def __init__(
        self,
        url: str | None = None,
        *,
        src: str = "discokit",
        client=None,
        retain_ttl: int = DEFAULT_RETAIN_TTL,
    ) -> None:
        self.url = url or os.environ.get("DISCOBOTS_BUS_URL") or os.environ.get("BUS_URL")
        self.src = src
        self.retain_ttl = retain_ttl
        self._client = client  # injectable (tests pass a fake); None ⇒ lazy-connect
        self._tried = client is not None

    # --- connection (lazy, best-effort) -------------------------------------
    @property
    def client(self):
        if self._client is None and not self._tried:
            self._tried = True
            if self.url:
                try:
                    import redis

                    self._client = redis.Redis.from_url(
                        self.url,
                        decode_responses=True,
                        socket_timeout=2,
                        socket_connect_timeout=2,
                    )
                except Exception as exc:  # noqa: BLE001 — a missing/unreachable bus must not crash
                    print(f"[bus] connect failed: {exc}", file=sys.stderr)
                    self._client = None
        return self._client

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _envelope(self, topic: str, type: str, data: object) -> dict:
        return {"v": 1, "ts": time.time(), "src": self.src, "topic": topic, "type": type, "data": data}

    # --- telemetry: pub/sub + retained last-value ---------------------------
    def publish(self, topic: str, data: object, *, type: str = "update", ttl: int | None = None) -> bool:
        """Fan-out PUBLISH + refresh the retained last-value. Fire-and-forget."""
        c = self.client
        if c is None:
            return False
        payload = json.dumps(self._envelope(topic, type, data))
        try:
            c.publish(topic, payload)
            c.set(f"{RETAIN_PREFIX}{topic}", payload, ex=ttl or self.retain_ttl)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[bus] publish {topic} failed: {exc}", file=sys.stderr)
            return False

    def retained(self, topic: str) -> dict | None:
        """The last published envelope for a topic, or None (bus down / expired)."""
        c = self.client
        if c is None:
            return None
        try:
            raw = c.get(f"{RETAIN_PREFIX}{topic}")
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            print(f"[bus] retained {topic} failed: {exc}", file=sys.stderr)
            return None

    # --- events: durable streams with consumer groups -----------------------
    def emit(self, stream: str, data: object, *, type: str = "event", maxlen: int = 10000) -> str | None:
        """XADD a durable event to a capped stream. Returns the id, or None."""
        c = self.client
        if c is None:
            return None
        try:
            env = self._envelope(stream, type, data)
            return c.xadd(f"{STREAM_PREFIX}{stream}", {"e": json.dumps(env)}, maxlen=maxlen, approximate=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[bus] emit {stream} failed: {exc}", file=sys.stderr)
            return None

    def read_group(
        self, stream: str, group: str, consumer: str, *, count: int = 16, block_ms: int = 0
    ) -> list[tuple[str, dict]]:
        """Consumer-group read (at-least-once): [(id, envelope), …]. ack() when done."""
        c = self.client
        if c is None:
            return []
        key = f"{STREAM_PREFIX}{stream}"
        try:
            try:
                c.xgroup_create(key, group, id="0", mkstream=True)
            except Exception:  # noqa: BLE001 — BUSYGROUP: the group already exists
                pass
            resp = c.xreadgroup(group, consumer, {key: ">"}, count=count, block=block_ms or None)
            out: list[tuple[str, dict]] = []
            for _key, entries in resp or []:
                for msg_id, fields in entries:
                    try:
                        out.append((msg_id, json.loads(fields["e"])))
                    except Exception:  # noqa: BLE001 — skip a malformed entry, keep the rest
                        pass
            return out
        except Exception as exc:  # noqa: BLE001
            print(f"[bus] read_group {stream} failed: {exc}", file=sys.stderr)
            return []

    def ack(self, stream: str, group: str, *ids: str) -> None:
        c = self.client
        if c is None or not ids:
            return
        try:
            c.xack(f"{STREAM_PREFIX}{stream}", group, *ids)
        except Exception as exc:  # noqa: BLE001
            print(f"[bus] ack failed: {exc}", file=sys.stderr)

    # --- coordination: distributed lock + windowed counter ------------------
    # These turn the bus from a message channel into a coordination layer for
    # the fleet's separate processes. Degradable and **fail-open**: with no bus
    # the lock always "acquires" (single-process semantics preserved — nothing
    # else can contend) and the counter returns None, so a broken bus can never
    # wedge a caller.
    def lock_acquire(self, name: str, *, ttl: int = 30, token: str | None = None) -> str | None:
        """Try to take a lock (SET NX EX). Returns a token on success, None if
        held elsewhere. A down/unreachable bus fails OPEN (returns a token)."""
        token = token or f"{os.getpid()}-{id(object())}"
        c = self.client
        if c is None:
            return token
        try:
            return token if c.set(f"lock:{name}", token, nx=True, ex=ttl) else None
        except Exception as exc:  # noqa: BLE001 — a broken bus must not wedge the caller
            print(f"[bus] lock {name} failed: {exc}", file=sys.stderr)
            return token

    def lock_release(self, name: str, token: str) -> None:
        """Release a lock only if we still hold it (compare-and-delete).

        A GET-then-DEL rather than a Lua CAS: the tiny window between the two is
        acceptable for a best-effort fleet lock (the TTL bounds any stale hold),
        and it works on any Redis-wire server without server-side scripting.
        """
        c = self.client
        if c is None:
            return
        try:
            key = f"lock:{name}"
            if c.get(key) == token:
                c.delete(key)
        except Exception as exc:  # noqa: BLE001
            print(f"[bus] unlock {name} failed: {exc}", file=sys.stderr)

    @contextlib.contextmanager
    def locked(self, name: str, *, ttl: int = 30):
        """`with bus.locked('job') as got:` — hold the lock for the block, release
        after. `got` is True if acquired, False if someone else holds it (skip)."""
        token = self.lock_acquire(name, ttl=ttl)
        try:
            yield token is not None
        finally:
            if token is not None:
                self.lock_release(name, token)

    def incr(self, name: str, *, window: int = 60, amount: int = 1) -> int | None:
        """A windowed counter (INCRBY + EXPIRE on first bump). Returns the new
        count, or None if the bus is down. For rate limits / live tallies — the
        key self-expires after `window` seconds."""
        c = self.client
        if c is None:
            return None
        try:
            key = f"count:{name}"
            n = c.incrby(key, amount)
            if n == amount:  # first bump in this window → set the TTL
                c.expire(key, window)
            return n
        except Exception as exc:  # noqa: BLE001
            print(f"[bus] incr {name} failed: {exc}", file=sys.stderr)
            return None


# --- debug CLI: publish/read the bus by hand (also a manual producer until the
#     supervisor publishes for real) -------------------------------------------
def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="discokit.bus debug CLI")
    ap.add_argument("--url", default=None, help="bus URL (else BUS_URL / DISCOBOTS_BUS_URL)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("publish", help="publish telemetry (topic + JSON data)")
    p.add_argument("topic")
    p.add_argument("json_data")

    g = sub.add_parser("retained", help="print a topic's retained value")
    g.add_argument("topic")

    e = sub.add_parser("emit", help="emit a durable event (stream + JSON data)")
    e.add_argument("stream")
    e.add_argument("json_data")

    r = sub.add_parser("read", help="consumer-group read a stream")
    r.add_argument("stream")
    r.add_argument("--group", default="debug")
    r.add_argument("--consumer", default="cli")

    args = ap.parse_args()
    bus = Bus(args.url, src="bus-cli")
    if not bus.enabled:
        print("[bus] no reachable bus (set BUS_URL)", file=sys.stderr)
        raise SystemExit(1)

    if args.cmd == "publish":
        print("published" if bus.publish(args.topic, json.loads(args.json_data)) else "failed")
    elif args.cmd == "retained":
        print(json.dumps(bus.retained(args.topic), indent=2))
    elif args.cmd == "emit":
        print(bus.emit(args.stream, json.loads(args.json_data)) or "failed")
    elif args.cmd == "read":
        for msg_id, env in bus.read_group(args.stream, args.group, args.consumer):
            print(msg_id, json.dumps(env))
            bus.ack(args.stream, args.group, msg_id)


if __name__ == "__main__":
    _main()
