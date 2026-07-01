# mcp — local MCP servers for the discobot fleet

Local (stdio) [MCP](https://modelcontextprotocol.io) servers that any Claude Code discobot
channel can load, registered globally in the mini's `~/.claude.json` → `mcpServers` (not
per-channel — see [`../DISCORD.md`](../DISCORD.md#mcp-servers-loaded-by-claude-discobot-channels)
for the registry entry and which channels can use each one).

Each server is a **single-file PEP 723 script** (`# /// script` inline metadata block) run via
`uv run --script <file>.py` — zero separate project/venv scaffolding, matching the
"simple reusable tool" framing these were built for.

## `obsidian_mcp.py`

Thin MCP client for the curated Obsidian CLI HTTP wrapper that runs on the Mac mini (a
separate service, not part of this repo). Exposes the wrapper's read ops
(`obsidian_read`, `obsidian_search`, `obsidian_files`, `obsidian_folders`,
`obsidian_backlinks`, `obsidian_tags`, `obsidian_tasks`, `obsidian_properties`,
`obsidian_daily`) and write ops (`obsidian_append`, `obsidian_create`,
`obsidian_set_property`) as MCP tools, one per wrapper route.

- **Config:** `OBSIDIAN_URL` env var, defaults to `http://127.0.0.1:8788` (loopback — this
  server is co-located with the wrapper on the mini). Set it to
  `https://tommys-mac-mini.tail59a169.ts.net/obsidian` if ever run off-mini.
- **Vault enum:** every tool's `vault` parameter is constrained to `home`, `dev`, `camping`,
  `gear`, `travel` — validated client-side (fast-fail) and, authoritatively, server-side by
  the wrapper. The surface is **not** vault-bound to one channel; a session can query any
  vault per call.
- **Error handling:** a wrapper 503 (mini's Obsidian isn't up — a normal, expected degrade)
  or a connection failure/timeout returns a short text error as the tool result. Nothing
  raises/crashes the MCP session.
- **No secrets.** The wrapper is tailnet/loopback-only with no auth token, so there's nothing
  to configure beyond `OBSIDIAN_URL`.

### Run standalone

```sh
uv run --script mcp/obsidian_mcp.py
```

Starts the stdio server and waits on stdin — exactly how Claude Code launches it as a
subprocess. `Ctrl-C` to stop.

### Tests

```sh
uv run --script mcp/tests/test_obsidian_mcp.py
```

Pure unit tests against a mocked HTTP transport (`httpx.MockTransport`) — no live wrapper
required. Covers request-building (params/body shape per route), response parsing, the
vault-enum fast-fail, and every error path (503, 4xx, connect error, timeout) degrading to a
clean text result instead of an exception.
