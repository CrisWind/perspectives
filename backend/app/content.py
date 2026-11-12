"""内容库：角色、思维框架、预设难题。内容在 app/content/*.yaml，由产品同学维护。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from .config import CONTENT_DIR

CATEGORIES = {"study": "升学", "job": "求职", "switch": "跳槽", "city": "换城市", "other": "其他"}
MBTI_TYPES = {a + b + c + d for a in "EI" for b in "SN" for c in "TF" for d in "JP"}


def norm_mbti(v: str | None) -> str | None:
    v = (v or "").strip().upper()
    return v if v in MBTI_TYPES else None


class Content:
    def __init__(self, directory: Path = CONTENT_DIR):
        raw = b""
        data: dict[str, list] = dict()
        for name in ("frameworks", "personas", "presets"):
            b = (directory / f"{name}.yaml").read_bytes()
            raw += b
            data[name] = yaml.safe_load(b) or []
        self.version = hashlib.sha1(raw).hexdigest()[:8]
        self.frameworks: dict[str, dict] = {f["id"]: f for f in data["frameworks"]}
        self.personas: dict[str, dict] = {}
        for p in data["personas"]:
            if p["framework"] not in self.frameworks:
                raise ValueError(f"persona {p['id']} uses unknown framework {p['framework']}")
            p.setdefault("can_search", False)
            p.setdefault("role", "debater")
            p.setdefault("rank", 50)
            self.personas[p["id"]] = p
        self.presets: dict[str, dict] = {p["id"]: p for p in data["presets"]}

    def framework_of(self, persona: dict) -> dict:
        return self.frameworks[persona["framework"]]

    def public_persona(self, p: dict, provider: str | None = None) -> dict:
        fw = self.framework_of(p)
        out = {
            "id": p["id"], "name": p["name"], "mbti": p.get("mbti") or None, "role": p["role"],
            "tagline": p.get("tagline", ""), "avatar": p.get("avatar", ""), "color": p.get("color", ""),
            "can_search": bool(p.get("can_search")),
            "framework": {"id": fw["id"], "name": fw["name"], "method": fw["method"]},
        }
        if provider:
            out["provider"] = provider
        return out


def select_team(content: Content, mbti: str | None, size: int, team_ids: list[str] | None = None) -> list[dict]:
    """按 MBTI 互补挑人：字母差异越多越优先，保证有一位查证员，框架不重复。

    结果是确定的（同样的输入得到同样的队伍），快速模式的缓存依赖这一点。
    """
    if team_ids:
        picked = [content.personas[i] for i in team_ids if i in content.personas]
        if picked:
            return picked
    debaters = sorted((p for p in content.personas.values() if p["role"] == "debater"), key=lambda p: p["rank"])
    mbti = norm_mbti(mbti)
    if mbti:
        def diff(p: dict) -> int:
            return sum(1 for a, b in zip(p["mbti"], mbti) if a != b)

        def tie(p: dict) -> str:  # 差异相同时按固定的伪随机顺序排，让每个角色都有出场机会
            return hashlib.sha1((mbti + p["id"]).encode()).hexdigest()

        ranked = sorted(debaters, key=lambda p: (-diff(p), tie(p)))
    else:
        ranked = debaters
    chosen: list[dict] = []
    searcher = next((p for p in ranked if p["can_search"]), None)
    if searcher:
        chosen.append(searcher)
    for p in ranked:
        if len(chosen) >= size:
            break
        if p in chosen or any(c["framework"] == p["framework"] for c in chosen):
            continue
        chosen.append(p)
    chosen.sort(key=lambda p: ranked.index(p))
    return chosen[:size]
