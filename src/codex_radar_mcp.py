#!/usr/bin/env python3
"""
Codex 雷达 MCP Server —— 把 codexradar.com 的实时模型 IQ / 社区评分 / 档位推荐
封装成 Model Context Protocol（Streamable HTTP）工具，供 MaiBot 等 MCP 客户端使用。

数据源：codexradar.com 的公开 JSON 接口（无需登录 / 无需 API key）。
设计目标：让 MaiBot 能直接回答“现在哪个模型的哪个档位最聪明、该换哪个档”。

作者：DemiLure（小深）· 为 Morphie 定制
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 全局配置
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("CODEX_RADAR_BASE_URL", "https://codexradar.com")
TIMEOUT = float(os.getenv("CODEX_RADAR_TIMEOUT", "15"))
HOST = os.getenv("CODEX_RADAR_HOST", "0.0.0.0")
PORT = int(os.getenv("CODEX_RADAR_PORT", "8765"))

# 已核验可用的公开 JSON 端点
ENDPOINTS = {
    "ratings": "/api/model-ratings",                           # 社区体感分
    "insights": "/api/radar-insights",                         # 综合智能 IQ + 场景推荐
    "intel_eff": "/api/intelligence-efficiency-metrics",       # 软件工程 IQ 明细
    "visual_spatial": "/api/visual-spatial-reasoning",         # 视觉空间 IQ
    "subscribers": "/api/subscriber-count",                    # 订阅人数
}

# 简洁的缓存：同一时间窗内多次查询不重复打远端
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = float(os.getenv("CODEX_RADAR_CACHE_TTL", "120"))  # 秒

# 推理档位中文对照（用于给 MaiBot 生成更友好的回答）
EFFORT_CN = {
    "ultra": "ultra(极尽)",
    "max": "max(最高)",
    "xhigh": "xhigh(特高)",
    "high": "high(高)",
    "medium": "medium(中)",
    "low": "low(低)",
    "off": "off(关闭)",
}

# 每个场景推荐（radar-insights 的 recommendation key → 用途说明），供工具描述用
SCENE_CN = {
    "daily_development": "日常开发",
    "hard_problems": "难题攻坚",
    "background_automation": "后台自动化",
    "lobster_tasks": "跑龙虾类任务",
}


# ---------------------------------------------------------------------------
# 抓取与缓存
# ---------------------------------------------------------------------------
async def _fetch_json(path: str) -> Any:
    """抓取远端 JSON，带 120s 缓存。网络失败时抛错而非返回脏数据。"""
    now = time.time()
    cached = _cache.get(path)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    url = BASE_URL + path
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()

    _cache[path] = (now, data)
    return data


async def _get(path: str) -> tuple[Any, str]:
    """抓取数据并返回 (data, 更新时间文案)。"""
    data = await _fetch_json(path)
    if isinstance(data, dict):
        updated = data.get("updated_at") or data.get("generated_at") or data.get("source_updated_at")
        if not updated:
            updated = data.get("day")
        return data, str(updated or "未知")
    return data, "未知"


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------
async def tool_ratings() -> dict[str, Any]:
    """社区体感分（近24小时）：各模型各档位的用户主观评分与评分人数。"""
    data, updated = await _get(ENDPOINTS["ratings"])
    models = data.get("models", []) if isinstance(data, dict) else []

    # 按模型分组，输出易读结构
    grouped: dict[str, list[dict[str, Any]]] = {}
    for m in models:
        grouped.setdefault(m.get("group", "未知"), []).append({
            "label": m.get("label"),
            "id": m.get("id"),
            "average": m.get("average"),
            "count": m.get("count"),
        })

    return {
        "ok": True,
        "day": data.get("day") if isinstance(data, dict) else None,
        "updated_at": updated,
        "refresh_seconds": data.get("refresh_seconds") if isinstance(data, dict) else None,
        "ratings": grouped,
    }


async def tool_insights() -> dict[str, Any]:
    """综合智能 IQ + 分场景档位推荐（日常开发/难题攻坚/后台自动化/跑龙虾）。"""
    data, updated = await _get(ENDPOINTS["insights"])
    recs = data.get("recommendations", []) if isinstance(data, dict) else []

    recommendations = []
    for rec in recs:
        items = []
        for it in rec.get("items", []):
            items.append({
                "model": it.get("model"),
                "effort": it.get("effort"),
                "effort_cn": EFFORT_CN.get(it.get("effort"), it.get("effort")),
                "iq": round(it.get("iq", 0), 2),
                "passed_pct": round(it.get("passed", 0), 2),
                "samples": it.get("samples"),
                "average_cost_usd": round(it.get("average_cost_usd", 0) or 0, 4),
                "average_duration_minutes": round(it.get("average_duration_minutes", 0) or 0, 2),
            })
        recommendations.append({
            "scene": rec.get("key"),
            "scene_cn": SCENE_CN.get(rec.get("key"), rec.get("key")),
            "title": rec.get("title"),
            "rule": rec.get("rule"),
            "items": items,
        })

    return {
        "ok": True,
        "generated_at": updated,
        "recommendation_mode": data.get("recommendation_mode") if isinstance(data, dict) else None,
        "recommendations": recommendations,
        "degradation_alerts": data.get("degradation_alerts", []) if isinstance(data, dict) else [],
    }


async def tool_intel_effort(model: str | None = None) -> dict[str, Any]:
    """软件工程能力 IQ 明细：通过率、平均费用、耗时、token、缓存命中率。

    Args:
        model: 可选，模型 id（如 gpt-5.6-sol）。留空返回全部。
    """
    data, updated = await _get(ENDPOINTS["intel_eff"])
    points = data.get("points", []) if isinstance(data, dict) else []

    if model:
        points = [p for p in points if p.get("model") == model]

    clean = []
    for p in points:
        clean.append({
            "model": p.get("model"),
            "effort": p.get("effort"),
            "effort_cn": EFFORT_CN.get(p.get("effort"), p.get("effort")),
            "iq": round(p.get("iq", 0), 2),
            "passed": p.get("passed"),
            "total": p.get("total"),
            "pass_rate_pct": round(p.get("passed", 0) / p.get("total", 1) * 100, 2) if p.get("total") else None,
            "average_price_usd": round(p.get("average_price_usd", 0) or 0, 4),
            "average_minutes": round(p.get("average_minutes", 0) or 0, 2),
            "average_total_tokens": p.get("average_total_tokens"),
            "cache_hit_rate": round(p.get("cache_hit_rate", 0) or 0, 4),
            "runs_total": p.get("runs_total"),
        })

    return {"ok": True, "updated_at": updated, "points": clean}


async def tool_visual_spatial(model: str | None = None) -> dict[str, Any]:
    """视觉空间推理智商（庞贝邻接 F1）：各模型各档位在视觉任务上的 IQ。"""
    data, updated = await _get(ENDPOINTS["visual_spatial"])
    points = data.get("points", []) if isinstance(data, dict) else []

    if model:
        points = [p for p in points if p.get("model") == model]

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
        })

    return {
        "ok": True,
        "benchmark_id": data.get("benchmark_id") if isinstance(data, dict) else None,
        "score_label": data.get("score_label") if isinstance(data, dict) else None,
        "updated_at": updated,
        "points": clean,
    }


async def tool_subscribers() -> dict[str, Any]:
    """Codex 雷达订阅人数。"""
    data, _ = await _get(ENDPOINTS["subscribers"])
    return {"ok": True, "count": data.get("count") if isinstance(data, dict) else data}


# ---------------------------------------------------------------------------
# MCP 装配
# ---------------------------------------------------------------------------
def build_server() -> FastMCP:
    mcp = FastMCP(
        name="codex-radar",
        host=HOST,
        port=PORT,
        streamable_http_path="/mcp",
        instructions=(
            "Codex 雷达（codexradar.com）实时模型智商数据。"
            "提供三类能力：1) 各模型各推理档位的智能 IQ（软件工程/视觉空间）；"
            "2) 分场景的档位推荐（日常开发/难题攻坚/后台自动化/跑龙虾，即‘建议换某档’）；"
            "3) 近24小时社区体感评分。可用于回答‘现在哪个模型最聪明/该换哪个档/有没有降智’等问题。"
        ),
    )

    @mcp.tool()
    async def get_model_ratings() -> dict[str, Any]:
        """获取 codexradar 近24小时社区体感评分（各模型各档位的用户主观分与人数）。"""
        return await tool_ratings()

    @mcp.tool()
    async def get_radar_insights() -> dict[str, Any]:
        """获取综合智能 IQ 与分场景档位推荐（含“建议换某档”的核心建议）。"""
        return await tool_insights()

    @mcp.tool()
    async def get_intelligence_effort(model: str | None = None) -> dict[str, Any]:
        """软件工程能力 IQ 明细。可选按 model id 过滤（如 gpt-5.6-sol / gpt-5.5 / deepseek-v4-pro）。"""
        return await tool_intel_effort(model)

    @mcp.tool()
    async def get_visual_spatial(model: str | None = None) -> dict[str, Any]:
        """视觉空间推理智商（庞贝邻接 F1）。可选按 model id 过滤。"""
        return await tool_visual_spatial(model)

    @mcp.tool()
    async def get_subscriber_count() -> dict[str, Any]:
        """Codex 雷达当前订阅人数。"""
        return await tool_subscribers()

    return mcp


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> None:
    server = build_server()
    print(f"[codex-radar-mcp] 启动 FastMCP Streamable HTTP: {server.streamable_http_app}")
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
