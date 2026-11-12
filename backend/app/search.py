"""联网查证。支持 Tavily 和博查；没配置时 enabled 为 False，查证角色会被告知不能引用来源。"""
from __future__ import annotations

import logging

import httpx

from .config import Settings
from .util import clip

log = logging.getLogger("perspectives.search")


class Search:
    def __init__(self, settings: Settings):
        self.provider = settings.search_provider
        self.key = settings.search_api_key
        self.enabled = bool(self.key) and self.provider in ("tavily", "bocha")

    async def search(self, query: str, k: int = 4) -> list[dict]:
        """返回 [{title, url, snippet}]。失败返回空列表，不影响辩论。"""
        if not self.enabled:
            return []
        headers = {"Authorization": f"Bearer {self.key}"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if self.provider == "tavily":
                    r = await client.post(
                        "https://api.tavily.com/search", headers=headers,
                        json={"query": query, "max_results": k},
                    )
                    r.raise_for_status()
                    items = r.json().get("results") or []
                    rows = [(i.get("title"), i.get("url"), i.get("content")) for i in items]
                else:
                    r = await client.post(
                        "https://api.bochaai.com/v1/web-search", headers=headers,
                        json={"query": query, "count": k, "summary": True},
                    )
                    r.raise_for_status()
                    pages = ((r.json().get("data") or dict()).get("webPages") or dict()).get("value") or []
                    rows = [(i.get("name"), i.get("url"), i.get("summary") or i.get("snippet")) for i in pages]
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            log.warning("search failed: %s", e)
            return []
        return [
            {"title": clip(t or "", 80), "url": u, "snippet": clip(s or "", 300)}
            for t, u, s in rows[:k] if u
        ]
