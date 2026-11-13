"""自进化，三层，都不改模型本身：

- 个人层：记住用户的价值取向和过去的决定（user_profiles）。
- 群体层：经验库，记录哪种追问方式在哪类决策里被用户认为有价值（experiences）。
- 系统层：角色提示词的补充准则，带版本，过门槛才上线，可回滚（prompt_versions）。

防讨好的规则写死在这里：
- 只收"这条论点有没有价值"的反馈；用户是否同意结论、是否改变主意，系统根本不收集。
- 支持用户倾向的论点即使被标为有价值，也不进经验库。
- 新的补充准则如果让挑战变少，过不了门槛。
"""
from __future__ import annotations

import logging

from . import prompts
from .llm import LLMError
from .quality import similarity
from .util import clip, now_iso

log = logging.getLogger("perspectives.evolution")

LABELS = ("unexpected", "valuable", "useless")  # 没想到 / 有用 / 没用
POSITIVE = ("unexpected", "valuable")


# ---------- 个人层 ----------
def _blank_profile() -> dict:
    return {"decisions": [], "values": dict(), "frameworks": dict()}


def load_profile(store, user_id: str | None) -> dict:
    row = store.get_profile(user_id) if user_id else None
    prof = (row or dict()).get("profile") or _blank_profile()
    for k, v in _blank_profile().items():
        prof.setdefault(k, v)
    return prof


def profile_note(store, user_id: str | None, exclude_sid: str | None = None) -> str:
    if not user_id:
        return ""
    prof = load_profile(store, user_id)
    lines = []
    past = [d for d in prof["decisions"] if d.get("session_id") != exclude_sid][-3:]
    for d in past:
        line = f"- 过去的决定（{d.get('date', '')[:10]}）：{clip(d.get('dilemma', ''), 50)}，当时倾向：{clip(d.get('leaning', ''), 30)}"
        if d.get("tradeoff"):
            line += f"，卡住的取舍：{d['tradeoff']}"
        lines.append(line)
    top = sorted(prof["values"].items(), key=lambda kv: -kv[1])[:4]
    if top:
        lines.append("- 过去表现出看重：" + "、".join(k for k, _ in top))
    return "\n".join(lines)


def on_completed(svc, session: dict, card: dict) -> None:
    """辩论结束后，把这次决定和看重的东西记进个人档案。"""
    uid = session.get("user_id")
    if not uid or session.get("mode") == "eval":
        return
    prof = load_profile(svc.store, uid)
    if not any(d.get("session_id") == session["id"] for d in prof["decisions"]):
        prof["decisions"].append({
            "session_id": session["id"], "date": now_iso(), "category": session.get("category"),
            "dilemma": clip(session["dilemma"], 120), "leaning": clip(session.get("leaning") or "", 60),
            "tradeoff": clip((card.get("core_tradeoff") or dict()).get("summary", ""), 80),
        })
        prof["decisions"] = prof["decisions"][-20:]
        for v in card.get("values") or []:
            prof["values"][v] = prof["values"].get(v, 0) + 1
        row = svc.store.get_profile(uid) or dict()
        svc.store.save_profile(uid, session.get("mbti") or row.get("mbti"), prof)
        svc.store.log_evolution("personal", "remembered", "记住了一位用户的这次决定和价值取向",
    {"session_id": session["id"], "values": card.get("values") or []})


# ---------- 群体层 ----------
def pick_experiences(store, category: str | None, persona: dict, k: int = 2) -> list[dict]:
    """给角色挑经验。可信才用：

    - 团队整理的种子经验（source=seed）直接可用。
    - 来自用户的经验至少被 2 个人认为有价值，并且有用率不低于一半，才会拿出来用；
                    只有一个人说过有用的，先留在库里观察，避免一个人的偏好变成所有人的建议。
    同一思维框架的优先。
    """
    rows = [e for e in store.experiences(category) if e["score"] >= 0.5 and (e.get("source") == "seed" or e["wins"] >= 2)]
    same = [e for e in rows if e["framework_id"] == persona["framework"]]
    rest = [e for e in rows if e["framework_id"] != persona["framework"]]
    return (same + rest)[:k]


async def apply_feedback(svc, sid: str, items: list[dict], user_id: str | None = None) -> dict:
    store = svc.store
    session = store.get_session(sid)
    claims = {c["id"]: c for c in store.claims(sid)}
    reasons = {r["id"]: r["text"] for r in session["reasons"]}
    applied: dict[str, list[int]] = dict()
    for e in store.events(sid, types=("experience.applied",)):
        for it in e["data"].get("items", []):
            applied.setdefault(it["persona_id"], []).append(it["id"])
    uid = user_id or session.get("user_id")
    category = session.get("category") or "other"
    result = {"accepted": 0, "ignored": 0, "learned": [], "reinforced": [], "skipped_support": 0}
    prof = load_profile(store, uid) if uid else None
    for item in items:
        claim = claims.get(item.get("claim_id"))
        label = item.get("label")
        if not claim or claim["persona_id"] == "user" or label not in LABELS:
            result["ignored"] += 1
            continue
        first = store.save_feedback(sid, claim["id"], label, uid)
        result["accepted"] += 1
        if not first or session.get("mode") == "eval":
            continue  # 改标签只更新记录，不重复计入进化
        persona = svc.content.personas.get(claim["persona_id"])
        custom = (session.get("options") or dict()).get("custom_persona")
        if not persona and custom and custom["id"] == claim["persona_id"]:
            persona = custom
        fw_id = persona["framework"] if persona else None
        positive = label in POSITIVE
        for exp_id in applied.get(claim["persona_id"], []):
            store.bump_experience(exp_id, wins=int(positive), losses=int(not positive))
        if prof is not None and fw_id:
            fw = prof["frameworks"].setdefault(fw_id, {"valued": 0, "useless": 0})
            fw["valued" if positive else "useless"] += 1
        if not positive or (persona and persona.get("custom")):
            continue  # 用户自建角色的论点只记个人偏好，不进共享经验库
        if claim["direction"] == "support":
            result["skipped_support"] += 1
            store.log_evolution("group", "skipped_support", "一条支持用户倾向的论点被标为有价值，按防讨好规则不进经验库",
                                {"session_id": sid, "claim_id": claim["id"]})
            continue
        pattern = clip(claim["text"], 60)
        try:
            system, user = prompts.generalize(category, claim["text"], reasons.get(claim.get("reason_id"), ""))
            out, _ = await svc.llm.json("generalize", system, user, provider=svc.llm.judge, session_id=sid,
            temperature=0.2, max_tokens=200, mock={"text": claim["text"]})
            if isinstance(out.get("pattern"), str) and len(out["pattern"].strip()) >= 6:
                pattern = clip(out["pattern"].strip(), 80)
        except LLMError as e:
            log.warning("generalize failed, using claim text: %s", e)
        existing = next((e for e in store.experiences(category) if similarity(e["pattern"], pattern) >= 0.6), None)
        if existing:
            store.bump_experience(existing["id"], wins=1)
            result["reinforced"].append({"id": existing["id"], "pattern": existing["pattern"]})
            store.log_evolution("group", "reinforced", f"经验被再次验证：{existing['pattern']}",
                                {"experience_id": existing["id"], "session_id": sid})
        else:
            exp_id = store.add_experience(category=category, framework_id=fw_id, persona_id=claim["persona_id"],
                            pattern=pattern, source_session=sid, source_claim=claim["id"])
            result["learned"].append({"id": exp_id, "pattern": pattern})
            store.log_evolution("group", "learned", f"学到一条新经验：{pattern}",
    {"experience_id": exp_id, "category": category, "framework_id": fw_id, "session_id": sid})
    if prof is not None:
        row = store.get_profile(uid) or dict()
        store.save_profile(uid, row.get("mbti") or session.get("mbti"), prof)
    return result


# ---------- 系统层 ----------
async def propose_patch(svc, persona_id: str) -> dict:
    """根据"没用"的反馈和被打回的记录，给角色起草一条补充准则（候选版本，还没上线）。"""
    store, persona = svc.store, svc.content.personas[persona_id]
    fw = svc.content.framework_of(persona)
    useless = store.labeled_claims(persona_id, ("useless",))
    valued = store.labeled_claims(persona_id, POSITIVE, limit=8)
    if not useless:
        raise ValueError("这个角色还没有被标为没用的论点，没有改进依据")
    active = store.patch_for(persona_id)
    system, user = prompts.patch(persona, fw, useless, valued, 0, active["patch"] if active else "")
    out, _ = await svc.llm.json("patch", system, user, provider=svc.llm.judge, temperature=0.4, max_tokens=300)
    text = str(out.get("patch") or "").strip()
    if len(text) < 6:
        raise ValueError("模型没有给出可用的补充准则")
    v = store.add_prompt_version(persona_id, clip(text, 300), clip(str(out.get("rationale") or ""), 200))
    store.log_evolution("system", "proposed", f"为「{persona['name']}」起草了第 {v['version']} 版补充准则",
                        {"version_id": v["id"], "patch": v["patch"], "based_on_useless": len(useless)})
    return v


async def evaluate_patch(svc, version_id: int, preset_ids: list[str] | None = None) -> dict:
    """门槛：在几道预设题上，分别用现有版本和候选版本各跑一遍，候选不差且挑战不变少才上线。"""
    from . import engine, metrics

    store = svc.store
    v = store.get_prompt_version(version_id)
    if not v or v["status"] != "candidate":
        raise ValueError("只能评测候选版本")
    pid = v["persona_id"]
    preset_ids = preset_ids or list(svc.content.presets)[:2]
    debaters = [p["id"] for p in svc.content.personas.values() if p["role"] == "debater" and p["id"] != pid]
    team = [pid] + debaters[: max(1, svc.settings.team_size - 1)]
    totals = {"base": {"score": 0.0, "challenges": 0}, "cand": {"score": 0.0, "challenges": 0}}
    for preset_id in preset_ids:
        for arm, override in (("base", dict()), ("cand", {pid: version_id})):
            sid = await engine.run_headless(svc, preset_id, {
                "team": team, "patch_overrides": override, "use_experience": False, "use_cache": False,
            })
            ps = metrics.persona_score(store, sid, pid)
            totals[arm]["score"] += ps["score"]
            totals[arm]["challenges"] += ps["challenges"]
    n = len(preset_ids)
    base, cand = totals["base"]["score"] / n, totals["cand"]["score"] / n
    passed = cand >= base and totals["cand"]["challenges"] >= totals["base"]["challenges"]
    if passed:
        old = store.patch_for(pid)
        if old:
            store.set_prompt_status(old["id"], "retired")
        store.set_prompt_status(version_id, "active", cand, base)
    else:
        store.set_prompt_status(version_id, "rejected", cand, base)
    name = svc.content.personas[pid]["name"]
    store.log_evolution(
        "system", "promoted" if passed else "rejected",
        f"「{name}」第 {v['version']} 版补充准则{'通过门槛，已上线' if passed else '没过门槛，未上线'}"
        f"（候选 {cand:.2f} 分，现有 {base:.2f} 分，{n} 道题）",
        {"version_id": version_id, "score": cand, "baseline_score": base, "presets": preset_ids,
                "challenges": {"candidate": totals["cand"]["challenges"], "baseline": totals["base"]["challenges"]}},
    )
    return store.get_prompt_version(version_id)


def rollback(svc, persona_id: str) -> dict:
    """撤下当前版本，恢复上一个上线过的版本（没有则回到原始设定）。"""
    store = svc.store
    active = store.patch_for(persona_id)
    if not active:
        raise ValueError("这个角色当前没有上线的补充准则")
    store.set_prompt_status(active["id"], "rolled_back")
    prev = store.patch_for(persona_id, "retired")
    if prev:
        store.set_prompt_status(prev["id"], "active")
    name = svc.content.personas[persona_id]["name"]
    store.log_evolution("system", "rolled_back",
                        f"「{name}」第 {active['version']} 版补充准则已回滚" + (f"，恢复第 {prev['version']} 版" if prev else "，恢复原始设定"),
                        {"version_id": active["id"], "restored": prev["id"] if prev else None})
    return {"rolled_back": active["id"], "restored": prev["id"] if prev else None}
