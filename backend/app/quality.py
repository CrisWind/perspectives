"""质量引擎：论点池去重、立场账本、人设检查。

每条发言先过确定性检查（格式、字面重复、立场变化有没有理由、引用来源是否真实），
再交给裁判模型做语义去重和人设检查。任何一项不过，发言被打回重写；重写次数用完后降级处理。
"""
from __future__ import annotations

import re

from . import prompts
from .llm import LLM, LLMError
from .util import clip

CLAIM_TYPES = ("new", "rebuttal", "support", "evidence")
DIRECTIONS = ("challenge", "support", "neutral")
POSITIONS = ("support", "oppose", "conditional")
_STRIP = re.compile(r"[\s\W_]+", re.U)
_REF = re.compile(r"回应\s*c\d+[:：]?")


def _grams(text: str) -> set[str]:
    t = _STRIP.sub("", _REF.sub("", text or "").lower())
    return {t[i : i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def similarity(a: str, b: str) -> float:
    """字面相似度（字符二元组）。取 Jaccard 和"短的被长的包含"两者中较大的。"""
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    jac = inter / len(ga | gb)
    small = min(len(ga), len(gb))
    contain = inter / small if small >= 8 else 0.0
    return max(jac, 0.85 * contain)


def most_similar(text: str, pool: list[dict]) -> tuple[dict | None, float]:
    best, score = None, 0.0
    for c in pool:
        s = similarity(text, c["text"])
        if s > score:
            best, score = c, s
    return best, score


def cluster(texts: list[str], threshold: float = 0.5) -> list[list[int]]:
    """贪心聚类，用于事后统计不重复论点数（所有对照组用同一把尺子）。"""
    groups: list[list[int]] = []
    for i, t in enumerate(texts):
        for g in groups:
            if similarity(t, texts[g[0]]) >= threshold:
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


def normalize(out: dict, reasons: list[dict], pool_ids: set[str], limit: int) -> tuple[dict | None, list[dict]]:
    """整理模型输出的格式。返回 (整理后的发言, 问题列表)；发言为 None 表示格式不可用。"""
    issues: list[dict] = []
    speech = str(out.get("speech") or "").strip()
    raw_claims = out.get("claims")
    if not speech or not isinstance(raw_claims, list) or not raw_claims:
        return None, [{"code": "format", "message": "输出缺少 speech 或 claims"}]
    reason_ids = {r["id"] for r in reasons}
    claims = []
    for c in raw_claims[:3]:
        if not isinstance(c, dict):
            continue
        text = str(c.get("text") or "").strip()
        if len(text) < 6:
            continue
        ctype = c.get("type") if c.get("type") in CLAIM_TYPES else "new"
        targets = c.get("targets")
        targets = targets if isinstance(targets, str) and targets in pool_ids else None
        if ctype != "new" and not targets:
            ctype = "new"
        claims.append({
            "type": ctype, "text": clip(text, 160),
            "reason_id": c.get("reason_id") if c.get("reason_id") in reason_ids else None,
            "targets": targets,
            "direction": c.get("direction") if c.get("direction") in DIRECTIONS else "neutral",
            "source": c.get("source") if isinstance(c.get("source"), str) else None,
        })
    if not claims:
        return None, [{"code": "format", "message": "没有可用的论点"}]
    if len(speech) > limit * 1.25:
        issues.append({"code": "too_long", "message": f"发言 {len(speech)} 字，超过 {limit} 字的限制"})
    st = out.get("stance") if isinstance(out.get("stance"), dict) else dict()
    try:
        conf = max(0.0, min(1.0, float(st.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    stance = {
        "position": st.get("position") if st.get("position") in POSITIONS else None,
        "confidence": conf,
        "persuaded_by": st.get("persuaded_by") if isinstance(st.get("persuaded_by"), str) else None,
        "note": clip(str(st.get("note") or ""), 120),
    }
    return {"speech": speech, "claims": claims, "stance": stance}, issues


def check_sources(claims: list[dict], allowed_urls: set[str]) -> list[dict]:
    """来源只能是查证结果里真实出现过的链接；否则去掉来源，证据类论点降级为普通论点。"""
    stripped = []
    for c in claims:
        src = c.pop("source", None)
        if src and src in allowed_urls:
            c["sources"] = [src]
        else:
            c["sources"] = []
            if src:
                stripped.append({"code": "fake_source", "message": "引用的来源不在查证结果里，已去掉", "claim_text": c["text"]})
        if c["type"] == "evidence" and not c["sources"] and not c["targets"]:
            c["type"] = "new"
    return stripped


def check_stance(stance: dict, prev: dict | None, persona_id: str, pool: list[dict]) -> dict | None:
    """立场账本：改变立场必须指明被哪条别人的论点说服。返回问题或 None，并在 stance 上写入 changed/justified。"""
    if stance["position"] is None:
        stance["position"] = prev["position"] if prev else "conditional"
    stance["changed"] = bool(prev) and stance["position"] != prev["position"]
    stance["justified"] = True
    stance["reverted"] = False
    stance["previous"] = prev["position"] if prev else None
    by = stance.get("persuaded_by")
    owner = next((c["persona_id"] for c in pool if c["id"] == by), None) if by else None
    if not stance["changed"]:
        if by and (owner is None or owner == persona_id):
            stance["persuaded_by"] = None
        return None
    if owner is None or owner == persona_id:
        stance["justified"] = False
        return {
            "code": "stance_unjustified",
            "message": f"立场从 {prev['position']} 变成 {stance['position']}，但没有指明被哪条别人的论点说服",
        }
    return None


def revert_stance(stance: dict, prev: dict) -> None:
    """重写次数用完仍说不出理由：账本不承认这次改变，立场保持原样。"""
    stance["position"] = prev["position"]
    stance["persuaded_by"] = None
    stance["changed"] = False
    stance["reverted"] = True
    stance["note"] = "改变立场但没有给出理由，立场账本保持原立场"


async def judge(
    llm: LLM, session_id: str, persona: dict, framework: dict, speech: str, claims: list[dict], pool: list[dict],
    position: str,
) -> tuple[dict[int, str], str | None, str | None]:
    """裁判模型：语义去重、人设检查、言行一致检查。返回 ({论点下标: 与之重复的论点编号}, 人设问题, 立场与发言矛盾的问题)。

    裁判调用失败时不拦截发言（宁可放过，也不让整场辩论卡住）。
    """
    system, user = prompts.judge(persona, framework, speech, claims, pool, position)
    try:
        out, _ = await llm.json(
            "judge", system, user, provider=llm.judge, session_id=session_id, temperature=0.0, max_tokens=700,
            mock={"claims": claims},
        )
    except LLMError:
        return dict(), None, None
    ids = {c["id"] for c in pool}
    dups: dict[int, str] = dict()
    for item in out.get("claims") or []:
        if not isinstance(item, dict):
            continue
        i = item.get("index")
        d = item.get("closest") if item.get("duplicate") is True else item.get("duplicate_of")
        if isinstance(i, int) and 0 <= i < len(claims) and isinstance(d, str) and d in ids:
            dups[i] = d
    per = out.get("persona") if isinstance(out.get("persona"), dict) else dict()
    issue = None
    if per.get("consistent") is False:
        issue = clip(str(per.get("issue") or "发言不符合人设"), 100)
    sj = out.get("stance") if isinstance(out.get("stance"), dict) else dict()
    mismatch = None
    if sj.get("consistent") is False:
        mismatch = clip(str(sj.get("issue") or "声明的立场和发言内容矛盾"), 100)
    return dups, issue, mismatch
