#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp", "httpx"]
# ///
"""obsidian_mcp — MCP (stdio) server exposing Tommy's Obsidian vaults to any Claude
discobot channel, via the curated Obsidian CLI HTTP wrapper running on the Mac mini.

Wrapper contract (already built/deployed on the mini — this server is a thin, faithful
client of it, nothing more):
  - Loopback (default, this server is co-located with the wrapper on the mini):
      http://127.0.0.1:8788
  - Tailnet (override via OBSIDIAN_URL if ever run off-mini):
      https://tommys-mac-mini.tail59a169.ts.net/obsidian
  - Vault allowlist (enum, nothing else is valid): home, dev, camping, gear, travel
  - Error envelope on any non-2xx: {"ok": false, "error": "<message>"}
  - 503 = mini's Obsidian isn't up — a NORMAL/EXPECTED degrade, not a bug.

This server does no business logic of its own: every tool is a direct pass-through to
one wrapper route, with (a) fast client-side vault-enum validation so a bad vault name
fails cheaply before a network call, and (b) clean error surfacing — a connection
failure, timeout, or wrapper error envelope becomes a short text error result handed
back to the calling Claude session, never a raised exception/traceback.

Run standalone:   uv run --script obsidian_mcp.py
Registered in Claude Code as a stdio MCP server (see DISCORD.md in this repo).
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_OBSIDIAN_URL = "http://127.0.0.1:8788"
OBSIDIAN_URL = os.environ.get("OBSIDIAN_URL", DEFAULT_OBSIDIAN_URL).rstrip("/")

# Per the contract's timing notes: CLI calls are normally sub-second, but opening a
# closed vault on the mini can add ~2-8s, and a fully-down Obsidian surfaces as a 503
# after ~10s. 20s gives comfortable headroom without hanging a Discord reply forever.
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=20.0)

# The wrapper's vault allowlist — nothing else is valid. Kept as a tuple (not a set) so
# it can double as the Literal type below and as a stable, orderable error message.
VAULTS: tuple[str, ...] = ("home", "dev", "camping", "gear", "travel")
Vault = Literal["home", "dev", "camping", "gear", "travel"]

mcp = FastMCP("obsidian")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Lazily create a single shared AsyncClient (one per process, reused across calls)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=OBSIDIAN_URL, timeout=HTTP_TIMEOUT)
    return _client


def _validate_vault(vault: str) -> str | None:
    """Fast client-side fail-fast check. Returns an error string if invalid, else None.
    The wrapper is the real gate (this just avoids a pointless round-trip for a typo)."""
    if vault not in VAULTS:
        return f"error: invalid vault {vault!r} — must be one of {', '.join(VAULTS)}"
    return None


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any] | str:
    """Shared request helper. Returns the parsed JSON body on success, or a short
    human-readable error string on any failure — never raises. Every tool function
    below is expected to check `isinstance(result, str)` for the error case."""
    try:
        resp = await _get_client().request(method, path, **kwargs)
    except httpx.TimeoutException:
        return (
            f"error: timed out reaching the Obsidian wrapper at {OBSIDIAN_URL}{path} "
            f"(mini may be busy opening the vault, or Obsidian is down — this is a "
            f"normal degrade, try again)"
        )
    except httpx.ConnectError:
        return (
            f"error: could not connect to the Obsidian wrapper at {OBSIDIAN_URL} "
            f"(is it running? this is expected if the mini's wrapper service is down)"
        )
    except httpx.HTTPError as exc:
        return f"error: HTTP failure calling the Obsidian wrapper: {exc}"

    try:
        body = resp.json()
    except ValueError:
        return f"error: wrapper returned non-JSON response (status {resp.status_code})"

    if resp.status_code == 503:
        return (
            "error: Obsidian is unavailable on the mini right now (503) — this is a "
            "normal, expected degrade (the mini's Obsidian isn't always up), not a bug. "
            f"wrapper said: {body.get('error', 'unavailable')}"
        )
    if not resp.is_success or body.get("ok") is False:
        return (
            f"error ({resp.status_code}): {body.get('error', 'unknown wrapper error')}"
        )

    return body


# ---------------------------------------------------------------------------
# Read tools (the must-haves)
# ---------------------------------------------------------------------------


@mcp.tool()
async def obsidian_read(vault: Vault, path: str) -> str:
    """Read the full text of one note from an Obsidian vault.

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        path: Vault-relative note path, must end in .md/.canvas/.base (e.g. "Trips/LAX.md").
    """
    if err := _validate_vault(vault):
        return err
    result = await _request("GET", "/read", params={"vault": vault, "path": path})
    if isinstance(result, str):
        return result
    return result.get("content", "")


@mcp.tool()
async def obsidian_search(
    vault: Vault, q: str, limit: int = 10, folder: str | None = None
) -> str:
    """Full-text search a vault; returns matching files with the matching line(s).

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        q: Search query (max 300 chars, enforced server-side).
        limit: Max results, 1-50 (default 10).
        folder: Optional folder to scope the search to.
    """
    if err := _validate_vault(vault):
        return err
    params: dict[str, Any] = {"vault": vault, "q": q, "limit": limit}
    if folder:
        params["folder"] = folder
    result = await _request("GET", "/search", params=params)
    if isinstance(result, str):
        return result
    results = result.get("results", [])
    if not results:
        return f"No matches for {q!r} in vault {vault!r}."
    lines = [f"{len(results)} file(s) matched {q!r} in vault {vault!r}:"]
    for r in results:
        lines.append(f"\n{r['file']}")
        for m in r.get("matches", []):
            lines.append(f"  L{m.get('line')}: {m.get('text', '').strip()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Additional read tools (nice-to-have coverage of the rest of the wrapper's GETs)
# ---------------------------------------------------------------------------


@mcp.tool()
async def obsidian_files(
    vault: Vault, folder: str | None = None, ext: str | None = None
) -> str:
    """List files in a vault (optionally scoped to a folder / filtered by extension).

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        folder: Optional folder to scope to.
        ext: Optional extension filter (e.g. "md").
    """
    if err := _validate_vault(vault):
        return err
    params: dict[str, Any] = {"vault": vault}
    if folder:
        params["folder"] = folder
    if ext:
        params["ext"] = ext
    result = await _request("GET", "/files", params=params)
    if isinstance(result, str):
        return result
    files = result.get("files", [])
    return "\n".join(files) if files else f"No files found in vault {vault!r}."


@mcp.tool()
async def obsidian_folders(vault: Vault, folder: str | None = None) -> str:
    """List folders in a vault (optionally scoped to a parent folder).

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        folder: Optional parent folder to scope to.
    """
    if err := _validate_vault(vault):
        return err
    params: dict[str, Any] = {"vault": vault}
    if folder:
        params["folder"] = folder
    result = await _request("GET", "/folders", params=params)
    if isinstance(result, str):
        return result
    folders = result.get("folders", [])
    return "\n".join(folders) if folders else f"No folders found in vault {vault!r}."


@mcp.tool()
async def obsidian_backlinks(vault: Vault, path: str) -> str:
    """List notes that link to a given note.

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        path: Vault-relative note path (e.g. "Gear/Truck.md").
    """
    if err := _validate_vault(vault):
        return err
    result = await _request("GET", "/backlinks", params={"vault": vault, "path": path})
    if isinstance(result, str):
        return result
    backlinks = result.get("backlinks", [])
    if not backlinks:
        return f"No backlinks to {path!r} in vault {vault!r}."
    return "\n".join(b["file"] for b in backlinks)


@mcp.tool()
async def obsidian_tags(vault: Vault, path: str | None = None) -> str:
    """List tags — vault-wide, or for one note if path is given.

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        path: Optional vault-relative note path to scope to a single file's tags.
    """
    if err := _validate_vault(vault):
        return err
    params: dict[str, Any] = {"vault": vault}
    if path:
        params["path"] = path
    result = await _request("GET", "/tags", params=params)
    if isinstance(result, str):
        return result
    tags = result.get("tags", [])
    return (
        ", ".join(t["tag"] for t in tags)
        if tags
        else f"No tags found in vault {vault!r}."
    )


@mcp.tool()
async def obsidian_tasks(
    vault: Vault,
    path: str | None = None,
    todo: bool | None = None,
    done: bool | None = None,
) -> str:
    """List checkbox tasks in a vault (optionally scoped to one note / filtered by status).

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        path: Optional vault-relative note path to scope to a single file's tasks.
        todo: Optional flag to filter to only open (TODO) tasks.
        done: Optional flag to filter to only completed tasks.
    """
    if err := _validate_vault(vault):
        return err
    params: dict[str, Any] = {"vault": vault}
    if path:
        params["path"] = path
    if todo is not None:
        params["todo"] = todo
    if done is not None:
        params["done"] = done
    result = await _request("GET", "/tasks", params=params)
    if isinstance(result, str):
        return result
    tasks = result.get("tasks", [])
    if not tasks:
        return f"No tasks found in vault {vault!r}."
    return "\n".join(f"{t['file']}:{t.get('line', '?')}  {t['text']}" for t in tasks)


@mcp.tool()
async def obsidian_properties(vault: Vault, path: str) -> str:
    """Get the front-matter properties of one note, as a flat key: value listing.

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        path: Vault-relative note path (e.g. "Gear/Truck.md").
    """
    if err := _validate_vault(vault):
        return err
    result = await _request("GET", "/properties", params={"vault": vault, "path": path})
    if isinstance(result, str):
        return result
    props = result.get("properties", {})
    if not props:
        return f"No properties found on {path!r} in vault {vault!r}."
    return "\n".join(f"{k}: {v}" for k, v in props.items())


@mcp.tool()
async def obsidian_daily(vault: Vault) -> str:
    """Read today's daily note from a vault.

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
    """
    if err := _validate_vault(vault):
        return err
    result = await _request("GET", "/daily", params={"vault": vault})
    if isinstance(result, str):
        return result
    return result.get("content", "")


# ---------------------------------------------------------------------------
# Write tools (nice-to-have — mutate vault content, used deliberately)
# ---------------------------------------------------------------------------


@mcp.tool()
async def obsidian_append(vault: Vault, path: str, content: str) -> str:
    """Append text to the end of an existing note.

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        path: Vault-relative note path (e.g. "Journal/Daily/2026-07-01.md").
        content: Text to append (max 20000 chars, enforced server-side).
    """
    if err := _validate_vault(vault):
        return err
    result = await _request(
        "POST", "/append", json={"vault": vault, "path": path, "content": content}
    )
    if isinstance(result, str):
        return result
    return result.get("message", "appended")


@mcp.tool()
async def obsidian_create(
    vault: Vault, path: str, content: str, overwrite: bool = False
) -> str:
    """Create a new note (or overwrite an existing one if overwrite=True).

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        path: Vault-relative note path, must end in .md/.canvas/.base.
        content: Note text (max 20000 chars, enforced server-side).
        overwrite: If True, replace an existing note at this path.
    """
    if err := _validate_vault(vault):
        return err
    body: dict[str, Any] = {"vault": vault, "path": path, "content": content}
    if overwrite:
        body["overwrite"] = True
    result = await _request("POST", "/create", json=body)
    if isinstance(result, str):
        return result
    return result.get("message", "created")


@mcp.tool()
async def obsidian_set_property(vault: Vault, path: str, name: str, value: str) -> str:
    """Set one front-matter property on a note.

    Args:
        vault: One of "home", "dev", "camping", "gear", "travel".
        path: Vault-relative note path (e.g. "Gear/Truck.md").
        name: Property name (e.g. "status").
        value: Property value to set.
    """
    if err := _validate_vault(vault):
        return err
    result = await _request(
        "POST",
        "/property",
        json={"vault": vault, "path": path, "name": name, "value": value},
    )
    if isinstance(result, str):
        return result
    return result.get("message", "set")


if __name__ == "__main__":
    mcp.run()
