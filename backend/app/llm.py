"""模型接入层：统一走 OpenAI 兼容接口，支持多家模型、限流、重试、失败后换家、用量统计。

没有配置任何密钥时使用内置模拟模型（app/mock.py），用于联调和测试。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import json as jsonlib
from dataclasses import dataclass, field

import httpx

from .config import Settings
from .mock import mock_response
from .util import extract_json

log = logging.getLogger("perspectives.llm")

# 默认地址和模型名。模型名可能过时，以各家控制台为准，用 名字_MODEL 环境变量覆盖。
KNOWN = {
    "deepseek": ("https://api.deepseek.com", "deepseek-chat"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-plus"),
    "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-32k"),
    "doubao": ("https://ark.cn-beijing.volces.com/api/v3", ""),
    "openai": ("https://api.openai.com/v1", ""),
    "mimo": ("https://api.xiaomimimo.com/v1", "mimo-v2.6-pro"),
}
# 各家特有的请求参数。MiMo 默认开启深度思考：会占用输出额度、拖慢速度，并且思考模式下温度不可调，所以默认关掉。
# 可用 名字_EXTRA_BODY 环境变量（JSON）覆盖，例如 MIMO_EXTRA_BODY={"thinking":{"type":"enabled"}}
DEFAULT_EXTRA = {
    "mimo": {"thinking": {"type": "disabled"}},
}


class LLMError(RuntimeError):
    pass


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    json_mode: bool = True
    extra: dict = field(default_factory=dict)


def _extra(name: str) -> dict:
    extra = dict(DEFAULT_EXTRA.get(name) or dict())
    raw = os.environ.get(f"{name.upper()}_EXTRA_BODY", "").strip()
    if raw:
        try:
            extra = dict(jsonlib.loads(raw))
        except (ValueError, TypeError):
            log.warning("%s_EXTRA_BODY is not a JSON object, ignored", name.upper())
    return extra


def load_providers() -> list[Provider]:
    names = list(KNOWN)
    for extra in os.environ.get("LLM_EXTRA_PROVIDERS", "").split(","):
        extra = extra.strip().lower()
        if extra and extra not in names:
            names.append(extra)
    out = []
    for name in names:
        key = os.environ.get(f"{name.upper()}_API_KEY", "").strip()
        if not key:
            continue
        base, model = KNOWN.get(name, ('', ''))
        base = os.environ.get(f"{name.upper()}_BASE_URL", "").strip() or base
        model = os.environ.get(f"{name.upper()}_MODEL", "").strip() or model
        # 一个网关背后有多个模型：NAME_MODELS=别名:模型名,别名:模型名，每个别名成为独立的提供方，共用同一个密钥和地址
        multi = os.environ.get(f"{name.upper()}_MODELS", "").strip()
        if multi and base:
            for pair in multi.split(","):
                alias, _, real = pair.strip().partition(":")
                if alias and real:
                    out.append(Provider(alias.strip().lower(), base.rstrip('/'), key, real.strip(), extra=_extra(alias.strip().lower())))
            continue
        if not base or not model:
            log.warning("provider %s skipped: needs %s_BASE_URL and %s_MODEL", name, name.upper(), name.upper())
            continue
        out.append(Provider(name, base.rstrip('/'), key, model, extra=_extra(name)))
    return out


class LLM:
    def __init__(self, settings: Settings, store=None, providers: list[Provider] | None = None):
        self.settings = settings
        self.store = store
        found = load_providers() if providers is None else providers
        self.providers: dict[str, Provider] = {p.name: p for p in found}
        self.mock = settings.force_mock or not self.providers
        self._sems: dict[str, asyncio.Semaphore] = dict()  # 每个提供方单独限流，一家慢不拖累别家
        self._client: httpx.AsyncClient | None = None

    # ---------- 路由 ----------
    @property
    def names(self) -> list[str]:
        return ["mock"] if self.mock else list(self.providers)

    @property
    def persona_pool(self) -> list[str]:
        if self.mock:
            return ["mock"]
        picked = [n for n in self.settings.persona_providers if n in self.providers]
        return picked or list(self.providers)

    @property
    def judge(self) -> str:
        if self.mock:
            return "mock"
        j = self.settings.judge_provider
        return j if j in self.providers else next(iter(self.providers))

    def persona_provider(self, index: int) -> str:
        pool = self.persona_pool
        return pool[index % len(pool)]

    def info(self) -> dict:
        return {
            "mock": self.mock,
            "providers": [
                {"name": p.name, "model": p.model} for p in self.providers.values()
            ] if not self.mock else [],
            "persona_providers": self.persona_pool,
            "judge_provider": self.judge,
        }

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ---------- 调用 ----------
    async def json(
        self,
        purpose: str,
        system: str,
        user: str,
        *,
        provider: str | None = None,
        session_id: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1400,
        mock: dict | None = None,
    ) -> tuple[dict, str]:
        """返回 (解析后的 JSON, 实际使用的模型名)。解析失败会重试一次。"""
        if self.mock:
            if self.settings.mock_delay:
                await asyncio.sleep(self.settings.mock_delay)
            self._log(session_id, purpose, "mock", "mock", 0, 0, 0, True, None)
            return mock_response(purpose, mock or dict()), "mock"
        last: Exception | None = None
        for attempt in range(2):
            text, used = await self.text(
                purpose, system, user, provider=provider, session_id=session_id,
                temperature=temperature if attempt == 0 else 0.3, max_tokens=max_tokens, json_mode=True,
            )
            try:
                return extract_json(text), used
            except ValueError as e:
                last = e
                log.warning("bad JSON from %s for %s: %s", used, purpose, e)
        raise LLMError(f"{purpose}: model did not return valid JSON ({last})")

    async def text(
        self,
        purpose: str,
        system: str,
        user: str,
        *,
        provider: str | None = None,
        session_id: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1400,
        json_mode: bool = False,
        mock: dict | None = None,
        history: list[dict] | None = None,
    ) -> tuple[str, str]:
        if self.mock:
            if self.settings.mock_delay:
                await asyncio.sleep(self.settings.mock_delay)
            self._log(session_id, purpose, "mock", "mock", 0, 0, 0, True, None)
            return str(mock_response(purpose, mock or dict()).get("text", "")), "mock"
        first = provider if provider in self.providers else self.judge
        order = [first] + [n for n in self.providers if n != first]
        errors = []
        for name in order:
            p = self.providers[name]
            try:
                return await self._call(p, purpose, system, user, session_id, temperature, max_tokens, json_mode, history), name
            except LLMError as e:
                errors.append(f"{name}: {e}")
                log.warning("provider %s failed for %s, trying next: %s", name, purpose, e)
        raise LLMError("all providers failed: " + " | ".join(errors))

    async def _call(self, p: Provider, purpose, system, user, session_id, temperature, max_tokens, json_mode, history=None) -> str:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.llm_timeout)
        body = {
            "model": p.model,
            "messages": [dict(role="system", content=system)] + list(history or []) + [dict(role="user", content=user)],
            "temperature": temperature,
            "max_tokens": max_tokens,
            **p.extra,
        }
        headers = {"Authorization": f"Bearer {p.api_key}", "Content-Type": "application/json"}
        err = "unknown"
        for attempt in range(self.settings.llm_max_retries + 1):
            if json_mode and p.json_mode:
                body["response_format"] = {"type": "json_object"}
            else:
                body.pop("response_format", None)
            t0 = time.monotonic()
            retryable = True
            try:
                sem = self._sems.setdefault(p.name, asyncio.Semaphore(self.settings.llm_concurrency))
                async with sem:
                    r = await self._client.post(f"{p.base_url}/chat/completions", json=body, headers=headers)
                ms = int((time.monotonic() - t0) * 1000)
                if r.status_code == 200:
                    data = r.json()
                    content = (data["choices"][0]["message"].get("content") or "").strip()
                    usage = data.get("usage") or dict()
                    if content:
                        self._log(session_id, purpose, p.name, p.model, usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0), ms, True, None)
                        return content
                    err = "empty response"
                else:
                    err = f"HTTP {r.status_code}: {r.text[:200]}"
                    if r.status_code == 400 and json_mode and p.json_mode:
                        p.json_mode = False  # 这家不支持 JSON 模式，之后靠提示词约束
                    elif r.status_code in (400, 401, 403, 404):
                        retryable = False
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                ms = int((time.monotonic() - t0) * 1000)
                err = f"{type(e).__name__}: {e}"
            self._log(session_id, purpose, p.name, p.model, 0, 0, ms, False, err)
            if not retryable:
                break
            if attempt < self.settings.llm_max_retries:
                await asyncio.sleep(1.5 * (attempt + 1))
        raise LLMError(err)

    def _log(self, session_id, purpose, provider, model, pt, ct, ms, ok, error) -> None:
        if self.store is None:
            return
        self.store.log_llm_call(
            session_id=session_id, purpose=purpose, provider=provider, model=model,
            prompt_tokens=pt, completion_tokens=ct, latency_ms=ms, ok=ok, error=error,
        )
