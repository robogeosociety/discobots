# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp", "httpx", "pytest", "pytest-asyncio"]
# ///
"""Unit tests for obsidian_mcp.py's tool functions — request-building and
response/error-parsing logic, with the HTTP layer mocked via httpx.MockTransport.
No live wrapper required (and none is assumed to be running).

Run: uv run --script tests/test_obsidian_mcp.py
 or: uv run --with mcp --with httpx --with pytest --with pytest-asyncio pytest tests/
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "obsidian_mcp.py"
spec = importlib.util.spec_from_file_location("obsidian_mcp", MODULE_PATH)
obsidian_mcp = importlib.util.module_from_spec(spec)
sys.modules["obsidian_mcp"] = obsidian_mcp
spec.loader.exec_module(obsidian_mcp)


def _install_transport(handler):
    """Point the module's shared client at a mocked transport for one test."""
    transport = httpx.MockTransport(handler)
    obsidian_mcp._client = httpx.AsyncClient(
        base_url=obsidian_mcp.OBSIDIAN_URL,
        timeout=obsidian_mcp.HTTP_TIMEOUT,
        transport=transport,
    )


@pytest.fixture(autouse=True)
def reset_client():
    yield
    obsidian_mcp._client = None


# ---------------------------------------------------------------------------
# Vault validation (client-side fast-fail)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_rejects_bad_vault():
    result = await obsidian_mcp.obsidian_read(vault="nope", path="a.md")
    assert "invalid vault" in result
    assert "home" in result and "travel" in result


@pytest.mark.asyncio
async def test_search_rejects_bad_vault_before_network():
    called = {"hit": False}

    def handler(request):
        called["hit"] = True
        return httpx.Response(200, json={"ok": True, "results": []})

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_search(vault="nowhere", q="x")
    assert "invalid vault" in result
    assert called["hit"] is False


# ---------------------------------------------------------------------------
# Happy path: request building + response parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_happy_path():
    def handler(request):
        assert request.url.path == "/read"
        assert request.url.params["vault"] == "gear"
        assert request.url.params["path"] == "Truck.md"
        return httpx.Response(
            200,
            json={"ok": True, "op": "read", "vault": "gear", "content": "hello note"},
        )

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_read(vault="gear", path="Truck.md")
    assert result == "hello note"


@pytest.mark.asyncio
async def test_search_happy_path_formats_results():
    def handler(request):
        assert request.url.params["q"] == "campground"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "results": [
                    {
                        "file": "a.md",
                        "matches": [{"line": 2, "text": "a campground here"}],
                    }
                ],
            },
        )

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_search(vault="camping", q="campground")
    assert "a.md" in result
    assert "L2" in result
    assert "campground" in result


@pytest.mark.asyncio
async def test_search_no_matches():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "results": []})

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_search(vault="dev", q="zzz")
    assert "No matches" in result


@pytest.mark.asyncio
async def test_properties_flattens_dict():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "ok": True,
                "properties": {"type": "task", "status": "TODO", "area": None},
            },
        )

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_properties(vault="dev", path="Tasks/x.md")
    assert "type: task" in result
    assert "status: TODO" in result
    assert "area: None" in result


@pytest.mark.asyncio
async def test_append_posts_json_body():
    def handler(request):
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body == {
            "vault": "home",
            "path": "Journal/Daily/x.md",
            "content": "note",
        }
        return httpx.Response(
            200, json={"ok": True, "message": "Appended to: Journal/Daily/x.md"}
        )

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_append(
        vault="home", path="Journal/Daily/x.md", content="note"
    )
    assert "Appended" in result


# ---------------------------------------------------------------------------
# Error envelope handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrapper_503_is_graceful_not_a_crash():
    def handler(request):
        return httpx.Response(503, json={"ok": False, "error": "vault not open"})

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_read(vault="home", path="a.md")
    assert result.startswith("error:")
    assert "normal, expected degrade" in result


@pytest.mark.asyncio
async def test_wrapper_400_surfaces_message():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "error": "bad path"})

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_read(vault="home", path="../etc/passwd")
    assert "error (400)" in result
    assert "bad path" in result


@pytest.mark.asyncio
async def test_connect_error_is_graceful():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_read(vault="home", path="a.md")
    assert result.startswith("error:")
    assert "could not connect" in result


@pytest.mark.asyncio
async def test_timeout_is_graceful():
    def handler(request):
        raise httpx.TimeoutException("timed out", request=request)

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_search(vault="home", q="x")
    assert result.startswith("error:")
    assert "timed out" in result


@pytest.mark.asyncio
async def test_404_not_found():
    def handler(request):
        return httpx.Response(404, json={"ok": False, "error": "not found"})

    _install_transport(handler)
    result = await obsidian_mcp.obsidian_read(vault="home", path="Missing.md")
    assert "error (404)" in result


# ---------------------------------------------------------------------------
# All tool functions are registered with the exact enum-constrained vault type
# ---------------------------------------------------------------------------


def test_all_tools_have_vault_enum_in_schema():
    import asyncio

    tool_names = [
        "obsidian_read",
        "obsidian_search",
        "obsidian_files",
        "obsidian_folders",
        "obsidian_backlinks",
        "obsidian_tags",
        "obsidian_tasks",
        "obsidian_properties",
        "obsidian_daily",
        "obsidian_append",
        "obsidian_create",
        "obsidian_set_property",
    ]
    tools = asyncio.run(obsidian_mcp.mcp.list_tools())
    found = {t.name: t for t in tools}
    assert set(tool_names) <= set(found.keys())
    for name in tool_names:
        schema = found[name].inputSchema
        vault_schema = schema["properties"]["vault"]
        # Literal["home", "dev", "camping", "gear", "travel"] renders as an enum
        assert set(vault_schema.get("enum", [])) == set(obsidian_mcp.VAULTS), name


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
