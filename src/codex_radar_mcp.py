#!/usr/bin/env python3
"""Codex Radar data source exposed as an MCP Streamable HTTP server."""
from __future__ import annotations

import asyncio
import functools
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import httpx
from mcp.server.fastmcp import Context, FastMCP

BASE_URL = os.getenv("CODEX_RADAR_BASE_URL", "https://codexradar.com").rstrip("/")
TIMEOUT = float(os.getenv("CODEX_RADAR_TIMEOUT", "15"))
HOST = os.getenv("CODEX_RADAR_HOST", "0.0.0.0")
PORT = int(os.getenv("CODEX_RADAR_PORT", "8765"))
CACHE_TTL = float(os.getenv("CODEX_RADAR_CACHE_TTL", "120"))
PROGRESS_ENABLED = os.getenv("CODEX_RADAR_PROGRESS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

ENDPOINTS = {
    "ratings": "/api/model-ratings?view=public",
    "insights": "/api/radar-insights",
    "intel_eff": "/api/intelligence-efficiency-metrics",
    "visual_spatial": "/api/visual-spatial-reasoning",
    "subscribers": "/api/subscriber-count",
}
EFFORT_CN = {"ultra": "ultra(极尽)", "max": "max(最高)", "xhigh": "xhigh(特高)", "high": "high(高)", "medium": "medium(中)", "low": "low(低)", "off": "off(关闭)"}
SCENE_CN = {"daily_development": "日常开发", "hard_problems": "难题攻坚", "background_automation": "后台自动化", "lobster_tasks": "跑龙虾类任务"}

_cache: dict[str, tuple[float, Any]] = {}
# One lock per endpoint: concurrent calls to the same endpoint are single-flight,
# while unrelated endpoints can still fetch in parallel.
_cache_locks: dict[str, asyncio.Lock] = {}
_cache_locks_guard = asyncio.Lock()
_client: httpx.AsyncClient | None = None


async def _get_cache_lock(path: str) -> asyncio.Lock:
    async with _cache_locks_guard:
        return _cache_locks.setdefault(path, asyncio.Lock())


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=TIMEOUT, headers={"Accept": "application/json"})
    return _client


async def _close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _fetch_json(path: str) -> Any:
    """Fetch JSON with a shared connection pool, TTL cache and single-flight lock."""
    now = time.monotonic()
    cache_lock = await _get_cache_lock(path)
    async with cache_lock:
        cached = _cache.get(path)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]
        # Keep the lock across the fetch: these endpoints are infrequent and this
        # prevents a burst of tool calls from stampeding the public origin.
        client = await _get_client()
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await client.get(BASE_URL + path)
                response.raise_for_status()
                data = response.json()
                _cache[path] = (time.monotonic(), data)
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.25)
        raise RuntimeError(f"上游数据源暂时不可用（{type(last_error).__name__}）") from last_error


async def _get(path: str) -> tuple[Any, str]:
    data = await _fetch_json(path)
    if isinstance(data, dict):
        updated = data.get("updated_at") or data.get("generated_at") or data.get("source_updated_at") or data.get("day")
        return data, str(updated or "未知")
    return data, "未知"


def _filter_model(points: list[dict[str, Any]], model: str | None) -> list[dict[str, Any]]:
    """Match model ids case-insensitively while retaining exact-id semantics."""
    if not model or not model.strip():
        return points
    wanted = model.strip().casefold()
    return [p for p in points if str(p.get("model", "")).strip().casefold() == wanted]


async def _progress(ctx: Context | None, message: str) -> None:
    if PROGRESS_ENABLED and ctx is not None:
        await ctx.report_progress(0, 1, message)


async def tool_ratings() -> dict[str, Any]:
    data, updated = await _get(ENDPOINTS["ratings"])
    models = data.get("models", []) if isinstance(data, dict) else []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in models:
        grouped.setdefault(item.get("group", "未知"), []).append({"label": item.get("label"), "id": item.get("id"), "average": item.get("average"), "count": item.get("count")})
    return {"ok": True, "day": data.get("day") if isinstance(data, dict) else None, "updated_at": updated, "refresh_seconds": data.get("refresh_seconds") if isinstance(data, dict) else None, "ratings": grouped}


async def tool_insights() -> dict[str, Any]:
    data, updated = await _get(ENDPOINTS["insights"])
    recs = data.get("recommendations", []) if isinstance(data, dict) else []
    recommendations = []
    for rec in recs:
        items = []
        for item in rec.get("items", []):
            items.append({"model": item.get("model"), "effort": item.get("effort"), "effort_cn": EFFORT_CN.get(item.get("effort"), item.get("effort")), "iq": round(item.get("iq", 0) or 0, 2), "passed_pct": round(item.get("passed", 0) or 0, 2), "samples": item.get("samples"), "average_cost_usd": round(item.get("average_cost_usd", 0) or 0, 4), "average_duration_minutes": round(item.get("average_duration_minutes", 0) or 0, 2)})
        recommendations.append({"scene": rec.get("key"), "scene_cn": SCENE_CN.get(rec.get("key"), rec.get("key")), "title": rec.get("title"), "rule": rec.get("rule"), "items": items})
    return {"ok": True, "generated_at": updated, "recommendation_mode": data.get("recommendation_mode") if isinstance(data, dict) else None, "recommendations": recommendations, "degradation_alerts": data.get("degradation_alerts", []) if isinstance(data, dict) else []}


async def tool_intel_effort(model: str | None = None) -> dict[str, Any]:
    data, updated = await _get(ENDPOINTS["intel_eff"])
    points = _filter_model(data.get("points", []) if isinstance(data, dict) else [], model)
    clean = []
    for p in points:
        total = p.get("total")
        clean.append({
            "model": p.get("model"),
            "effort": p.get("effort"),
            "effort_cn": EFFORT_CN.get(p.get("effort"), p.get("effort")),
            "iq": round(p.get("iq", 0) or 0, 2),
            "passed": p.get("passed"),
            "total": total,
            "pass_rate_pct": round(p.get("passed", 0) / total * 100, 2) if total else None,
            "average_price_usd": round(p.get("average_price_usd", 0) or 0, 4),
            "average_price_usd_by_band": p.get("average_price_usd_by_band"),
            "average_minutes": round(p.get("average_minutes", 0) or 0, 2),
            "average_total_tokens": p.get("average_total_tokens"),
            "cache_hit_rate": round(p.get("cache_hit_rate", 0) or 0, 4),
            "combined_cost_index": round(p.get("combined_cost_index", 0) or 0, 4),
            "average_agent_steps": round(p.get("average_agent_steps", 0) or 0, 2),
            "agent_steps_samples": p.get("agent_steps_samples"),
            "token_samples": p.get("token_samples"),
            "cache_token_samples": p.get("cache_token_samples"),
            "runs_24h": p.get("runs_24h"),
            "runs_48h": p.get("runs_48h"),
            "runs_total": p.get("runs_total"),
        })
    return {"ok": True, "updated_at": updated, "points": clean}


async def tool_visual_spatial(model: str | None = None) -> dict[str, Any]:
    data, updated = await _get(ENDPOINTS["visual_spatial"])
    points = _filter_model(data.get("points", []) if isinstance(data, dict) else [], model)
    clean = []
    for p in points:
        clean.append({
            "model": p.get("model"),
            "effort": p.get("effort"),
            "effort_cn": EFFORT_CN.get(p.get("effort"), p.get("effort")),
            "iq": round(p.get("iq", 0) or 0, 2),
            "score": round(p.get("passed", 0) or 0, 2),
            "valid_tasks": p.get("valid_tasks"),
            "average_price_usd": round(p.get("average_price_usd", 0) or 0, 4),
            "average_minutes": round(p.get("average_minutes", 0) or 0, 2),
            "average_total_tokens": p.get("average_total_tokens"),
            "cache_hit_rate": round(p.get("cache_hit_rate", 0) or 0, 4),
            "combined_cost_index": round(p.get("combined_cost_index", 0) or 0, 4),
            "average_agent_steps": round(p.get("average_agent_steps", 0) or 0, 2),
            "agent_steps_samples": p.get("agent_steps_samples"),
            "token_samples": p.get("token_samples"),
            "cache_token_samples": p.get("cache_token_samples"),
            "runs_24h": p.get("runs_24h"),
            "runs_48h": p.get("runs_48h"),
            "runs_total": p.get("runs_total"),
        })
    return {"ok": True, "benchmark_id": data.get("benchmark_id") if isinstance(data, dict) else None, "score_label": data.get("score_label") if isinstance(data, dict) else None, "updated_at": updated, "points": clean}


async def tool_subscribers() -> dict[str, Any]:
    data, _ = await _get(ENDPOINTS["subscribers"])
    return {"ok": True, "count": data.get("count") if isinstance(data, dict) else data}


@asynccontextmanager
async def lifespan(_: FastMCP) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await _close_client()


F = TypeVar("F", bound=Callable[..., Awaitable[dict[str, Any]]])


def _safe_tool(coro: F) -> F:
    @functools.wraps(coro)
    async def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await coro(*args, **kwargs)
        except Exception as exc:
            # Keep upstream URLs/response bodies out of the client-facing result.
            return {"ok": False, "error": "无法获取 Codex Radar 数据", "detail": type(exc).__name__}
    return wrapped  # type: ignore[return-value]


def build_server() -> FastMCP:
    mcp = FastMCP(name="codex-radar", host=HOST, port=PORT, streamable_http_path="/mcp", lifespan=lifespan, instructions="Codex 雷达实时模型数据。优先使用 get_radar_insights 获取综合 IQ 和按场景推荐的模型/推理档位；其余工具用于社区评分、软件工程 IQ、视觉空间 IQ和订阅人数。数据带时间戳，仅代表源站最新快照。")

    @mcp.tool()
    @_safe_tool
    async def get_model_ratings(ctx: Context) -> dict[str, Any]:
        """获取近24小时社区体感评分；用于比较模型实际使用感受。"""
        await _progress(ctx, "收到！正在调用 get_model_ratings 工具……")
        return await tool_ratings()

    @mcp.tool()
    @_safe_tool
    async def get_radar_insights(ctx: Context) -> dict[str, Any]:
        """核心推荐工具：获取综合 IQ，并按日常开发、难题攻坚、后台自动化、跑龙虾推荐模型和推理档位。"""
        await _progress(ctx, "收到！正在调用 get_radar_insights 工具……")
        return await tool_insights()

    @mcp.tool()
    @_safe_tool
    async def get_intelligence_effort(model: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
        """获取软件工程 IQ 明细（通过率、费用、耗时、token、缓存命中率、成本指数、agent 步数、24/48h 运行量）；model 可选且不区分大小写。"""
        await _progress(ctx, "收到！正在调用 get_intelligence_effort 工具……")
        return await tool_intel_effort(model)

    @mcp.tool()
    @_safe_tool
    async def get_visual_spatial(model: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
        """获取视觉空间推理 IQ（庞贝邻接 F1，含成本指数、agent 步数、24/48h 运行量）；model 可选且不区分大小写。"""
        await _progress(ctx, "收到！正在调用 get_visual_spatial 工具……")
        return await tool_visual_spatial(model)

    @mcp.tool()
    @_safe_tool
    async def get_subscriber_count(ctx: Context) -> dict[str, Any]:
        """获取 Codex Radar 当前订阅人数。"""
        await _progress(ctx, "收到！正在调用 get_subscriber_count 工具……")
        return await tool_subscribers()

    return mcp


def main() -> None:
    build_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
