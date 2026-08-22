from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src import codex_radar_mcp as radar


@pytest.fixture(autouse=True)
def reset_state():
    radar._cache.clear()
    yield
    radar._cache.clear()


def test_filter_model_is_trimmed_and_case_insensitive():
    points = [{"model": "gpt-5.6-sol", "effort": "high"}, {"model": "gpt-5.5", "effort": "low"}]
    assert radar._filter_model(points, " GPT-5.6-SOL ") == [points[0]]
    assert radar._filter_model(points, "unknown") == []
    assert radar._filter_model(points, "   ") == points


@pytest.mark.asyncio
async def test_fetch_json_uses_cache_and_shared_client():
    response = httpx.Response(200, json={"value": 42}, request=httpx.Request("GET", "https://example.test/data"))
    client = AsyncMock()
    client.get.return_value = response

    with patch.object(radar, "_get_client", new=AsyncMock(return_value=client)), patch.object(radar, "BASE_URL", "https://example.test"):
        assert await radar._fetch_json("/data") == {"value": 42}
        assert await radar._fetch_json("/data") == {"value": 42}

    client.get.assert_awaited_once_with("https://example.test/data")


@pytest.mark.asyncio
async def test_fetch_json_retries_once_on_http_error():
    failed = httpx.Response(503, request=httpx.Request("GET", "https://example.test/data"))
    succeeded = httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", "https://example.test/data"))
    client = AsyncMock()
    client.get.side_effect = [failed, succeeded]

    with patch.object(radar, "_get_client", new=AsyncMock(return_value=client)), patch.object(radar.asyncio, "sleep", new=AsyncMock()), patch.object(radar, "BASE_URL", "https://example.test"):
        assert await radar._fetch_json("/data") == {"ok": True}

    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_tool_output_handles_missing_total():
    with patch.object(radar, "_get", new=AsyncMock(return_value=({"points": [{"model": "x", "passed": 3, "total": 0}]}, "now"))):
        result = await radar.tool_intel_effort()
    assert result["ok"] is True
    assert result["points"][0]["pass_rate_pct"] is None


@pytest.mark.asyncio
async def test_concurrent_cache_miss_is_single_flight():
    response = httpx.Response(200, json={"value": 1}, request=httpx.Request("GET", "https://example.test/data"))
    client = AsyncMock()

    async def get(_url):
        await asyncio.sleep(0.01)
        return response

    client.get.side_effect = get
    with patch.object(radar, "_get_client", new=AsyncMock(return_value=client)), patch.object(radar, "BASE_URL", "https://example.test"):
        results = await asyncio.gather(*(radar._fetch_json("/data") for _ in range(5)))

    assert results == [{"value": 1}] * 5
    assert client.get.await_count == 1
