"""指标：所有运行方式（我们的系统和各对照组）用同一套算法事后统计。"""
from __future__ import annotations

from .quality import cluster
from .store import Store


def claim_metrics(claims: list[dict], reasons: list[dict]) -> dict:
    """claims 是角色提出的论点（不含用户插话）。"""
    total = len(claims)
    groups = cluster([c["text"] for c in claims]) if claims else []
    unique = len(groups)
    challenges = sum(1 for c in claims if c.get("direction") == "challenge")
    later = [c for c in claims if c.get("round", 1) >= 2]
    covered = {c.get("reason_id") for c in claims if c.get("reason_id")}
    return {
        "claims": total,
        "unique_claims": unique,
        "repetition_rate": round(1 - unique / total, 3) if total else 0.0,
        "challenge_claims": challenges,
        "challenge_ratio": round(challenges / total, 3) if total else 0.0,
        "interaction_rate": round(sum(1 for c in later if c.get("targets")) / len(later), 3) if later else 0.0,
        "reason_coverage": round(len(covered & {r["id"] for r in reasons}) / len(reasons), 3) if reasons else 0.0,
    }


def session_metrics(store: Store, sid: str) -> dict:
    s = store.get_session(sid)
    claims = [c for c in store.claims(sid) if c["persona_id"] != "user"]
    stances = store.stances(sid)
    utts = store.utterances(sid)
    rejected = store.events(sid, types=("quality.rejected",))
    by_code: dict[str, int] = dict()
    for e in rejected:
        for i in e["data"].get("issues", []):
            by_code[i["code"]] = by_code.get(i["code"], 0) + 1
    m = claim_metrics(claims, s["reasons"])
    m.update({
        "utterances": len(utts),
        "echoes": sum(len(c["echoed_by"]) for c in claims),
        "stance_changes": sum(1 for x in stances if x["changed"]),
        "unjustified_stance_changes": sum(1 for x in stances if x["changed"] and not x["justified"]),
        "blocked_stance_changes": sum(1 for x in stances if x["reverted"]),
        "persona_flags": sum(1 for u in utts if u["persona_flag"]),
        "rewrites": len(rejected),
        "rejections_by_reason": by_code,
        "dropped_utterances": len(store.events(sid, types=("utterance.dropped",))),
        "quality_engine": bool((s.get("options") or dict()).get("quality_engine", True)),
        "llm": store.llm_usage(sid),
    })
    return m


def persona_score(store: Store, sid: str, persona_id: str) -> dict:
    """提示词门槛用的单角色得分：有效论点和挑战加分，被打回和人设偏离扣分。"""
    claims = [c for c in store.claims(sid) if c["persona_id"] == persona_id]
    utts = [u for u in store.utterances(sid) if u["persona_id"] == persona_id]
    rejected = [e for e in store.events(sid, types=("quality.rejected",)) if e["data"].get("persona_id") == persona_id]
    challenges = sum(1 for c in claims if c["direction"] == "challenge")
    flags = sum(1 for u in utts if u["persona_flag"])
    score = len(claims) + 0.5 * challenges - 0.5 * len(rejected) - flags
    return {"score": round(score, 2), "claims": len(claims), "challenges": challenges,
            "rewrites": len(rejected), "persona_flags": flags}
