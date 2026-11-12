"""评测：我们的系统和三个对照组在同一批难题上的对比。

  A  单个 AI 直接回答
  B  一段提示词让一个模型扮演多个角色
  C  多智能体，但关掉质量引擎
  ours  完整系统

所有方案的输出用同一套办法度量：同一个抽取器抽论点，同一个聚类器数不重复论点，
同一个裁判判人设。这样我们的系统不会因为"自己报的论点数"占便宜。

用法（在 backend 目录下）：
  uv run python scripts/run_eval.py      # 用预设难题
  uv run python scripts/run_eval.py --questions q.yaml # 用自己的题库（格式同 app/content/presets.yaml）
  uv run python scripts/run_eval.py --limit 3 --arms A,ours
结果写到 eval_out/：results.json、summary.md、blind/（盲评材料和答案）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import engine, prompts, quality  # noqa: E402
from app.config import BACKEND_DIR, Settings  # noqa: E402
from app.content import select_team  # noqa: E402
from app.engine import Services  # noqa: E402
from app.llm import LLMError  # noqa: E402

ARMS = ("A", "B", "C", "ours")
ARM_NAMES = {"A": "A 单个 AI", "B": "B 一个模型扮多角色", "C": "C 多智能体无质量引擎", "ours": "Perspectives"}
METRICS = [
    ("unique_claims", "不重复论点数"),
    ("repetition_rate", "重复率"),
    ("challenge_claims", "针对用户倾向的挑战数"),
    ("persona_consistency", "人设一致率"),
    ("unjustified_stance_changes", "无理由改变立场次数"),
]


def case_of(preset: dict) -> dict:
    return {
        "dilemma": preset["dilemma"], "leaning": preset["leaning"], "facts": preset.get("facts") or [],
        "reasons": [{"id": f"r{i + 1}", "text": t} for i, t in enumerate(preset["reasons"])], "questions": [],
    }


async def measure(svc: Services, speeches: list[dict], team: dict[str, dict]) -> dict:
    """speeches: [{persona: 角色名或 None, text}]。返回统一口径的指标。"""
    llm = svc.llm
    claims: list[dict] = []

    async def extract(sp: dict) -> list[dict]:
        system, user = prompts.extract(sp["text"])
        try:
            out, _ = await llm.json("extract", system, user, provider=llm.judge, temperature=0.0, max_tokens=900,
        mock={"text": sp["text"]})
        except LLMError:
            return []
        rows = []
        for c in out.get("claims") or []:
            if isinstance(c, dict) and isinstance(c.get("text"), str) and len(c["text"].strip()) >= 6:
                rows.append({"text": c["text"].strip(), "challenge": bool(c.get("challenge"))})
            elif isinstance(c, str) and len(c.strip()) >= 6:
                rows.append({"text": c.strip(), "challenge": False})
        return rows

    for rows in await asyncio.gather(*(extract(sp) for sp in speeches)):
        claims.extend(rows)
    texts = [c["text"] for c in claims]
    groups = quality.cluster(texts)
    if texts and not llm.mock:
        system, user = prompts.cluster(texts)
        try:
            out, _ = await llm.json("cluster", system, user, provider=llm.judge, temperature=0.0, max_tokens=1200)
            seen, got = set(), []
            for g in out.get("groups") or []:
                g = [i for i in g if isinstance(i, int) and 0 <= i < len(texts) and i not in seen]
                seen.update(g)
                if g:
                    got.append(g)
            got += [[i] for i in range(len(texts)) if i not in seen]
            groups = got
        except LLMError:
            pass
    challenge_groups = sum(1 for g in groups if any(claims[i]["challenge"] for i in g))

    async def consistent(sp: dict) -> bool | None:
        p = team.get(sp.get("persona") or "")
        if not p:
            return None
        system, user = prompts.persona_score(p, svc.content.framework_of(p), sp["text"])
        try:
            out, _ = await llm.json("persona_score", system, user, provider=llm.judge, temperature=0.0, max_tokens=60)
        except LLMError:
            return None
        return out.get("consistent") is not False

    marks = [m for m in await asyncio.gather(*(consistent(sp) for sp in speeches)) if m is not None]
    total = len(texts)
    return {
        "claims": total,
        "unique_claims": len(groups),
        "repetition_rate": round(1 - len(groups) / total, 3) if total else 0.0,
        "challenge_claims": challenge_groups,
        "persona_consistency": round(sum(marks) / len(marks), 3) if marks else None,
    }


async def arm_a(svc: Services, case: dict, team: list[dict]) -> dict:
    system, user = prompts.baseline_a(case)
    text, _ = await svc.llm.text("baseline_a", system, user, provider=svc.llm.persona_pool[0], max_tokens=1500,
                        mock={"leaning": case["leaning"]})
    return {"speeches": [{"persona": None, "text": text}], "final": text, "unjustified_stance_changes": None}


async def arm_b(svc: Services, case: dict, team: list[dict], rounds: int) -> dict:
    system, user = prompts.baseline_b(case, team, svc.content.frameworks, rounds)
    out, _ = await svc.llm.json("baseline_b", system, user, provider=svc.llm.persona_pool[0], max_tokens=4000,
                mock={"reasons": case["reasons"], "names": [p["name"] for p in team], "rounds": rounds})
    turns = [t for t in out.get("turns") or [] if isinstance(t, dict) and t.get("speech")]
    last: dict[str, str] = dict()
    unjustified = 0
    for t in turns:
        who, pos = t.get("persona"), t.get("position")
        if who in last and pos and pos != last[who] and not t.get("persuaded_by"):
            unjustified += 1
        if pos:
            last[who] = pos
    return {
        "speeches": [{"persona": t.get("persona"), "text": str(t["speech"])} for t in turns],
        "final": str(out.get("conclusion") or ""), "unjustified_stance_changes": unjustified,
    }


def render_card(card: dict) -> str:
    lines = ["理由体检："]
    lines += [f"- {rc['reason']}：{rc['verdict']}。{rc['summary']}" for rc in card["reason_checks"]]
    lines += ["漏掉的视角："] + [f"- {b['text']}" for b in card["blind_spots"]]
    lines += ["真正的取舍：" + card["core_tradeoff"]["summary"], "建议："]
    lines += [f"- {r['condition']}，{r['suggestion']}" for r in card["recommendations"]]
    return "\n".join(lines)


async def arm_multi(svc: Services, preset_id: str, engine_on: bool, rounds: int) -> dict:
    sid = await engine.run_headless(svc, preset_id, {
        "quality_engine": engine_on, "use_experience": False, "use_cache": False, "rounds": rounds,
    })
    s = svc.store.get_session(sid)
    if s["status"] != "completed":
        raise RuntimeError(f"session {sid} ended as {s['status']}")
    names = {p["id"]: p["name"] for p in svc.content.personas.values()}
    stances = svc.store.stances(sid)
    return {
        "session_id": sid,
        "speeches": [{"persona": names[u["persona_id"]], "text": u["speech"]} for u in svc.store.utterances(sid)],
        "final": render_card(s["card"]),
        "unjustified_stance_changes": sum(1 for x in stances if x["changed"] and not x["justified"]),
        "rewrites": len(svc.store.events(sid, types=("quality.rejected",))),
        "llm": svc.store.llm_usage(sid),
    }


def mean(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def write_outputs(out_dir: Path, results: list[dict], arms: list[str], mock: bool, seed: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(results)
    lines = [f"# 评测结果（{n} 道题）", ""]
    if mock:
        lines += ["**注意：这是用模拟模型跑的，数字没有意义，只说明评测流程能跑通。**", ""]
    lines += ["| 指标 | " + " | ".join(ARM_NAMES[a] for a in arms) + " |", "|---|" + "---|" * len(arms)]
    for key, label in METRICS:
        cells = []
        for a in arms:
            m = mean([r["arms"][a]["metrics"].get(key) for r in results if a in r["arms"]])
            cells.append("不适用" if m is None else str(m))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    failed = [(r["id"], a, v["error"]) for r in results for a, v in r["failed"].items()]
    lines += ["", "每项是各题的平均值。样本量小，只能说\"在这 N 道题上的对比结果\"，不能说显著优于。"]
    if failed:
        lines += ["", "以下运行失败，未计入平均："] + [f"- {q} / {a}：{e}" for q, a, e in failed]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    blind = out_dir / "blind"
    blind.mkdir(exist_ok=True)
    rng = random.Random(seed)
    key = dict()
    for r in results:
        order = [a for a in arms if a in r["arms"]]
        rng.shuffle(order)
        key[r["id"]] = {f"方案{i + 1}": a for i, a in enumerate(order)}
        doc = [f"# {r['title']}", "", r["dilemma"], "", "请选出对当事人最有帮助的方案，并写一句理由。", ""]
        for i, a in enumerate(order):
            doc += [f"## 方案{i + 1}", "", r["arms"][a]["final"], ""]
        (blind / f"{r['id']}.md").write_text("\n".join(doc), encoding="utf-8")
    (blind / "_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", help="题库 yaml，格式同 presets.yaml；不给则用预设难题")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", default=str(BACKEND_DIR / "eval_out"))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    arms = [a for a in args.arms.split(",") if a in ARMS]
    settings = Settings.from_env()
    settings.db_path = str(Path(args.out) / "eval.db")
    svc = Services(settings)
    if args.questions:
        qs = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))
        svc.content.presets = {q["id"]: q for q in qs}
    presets = list(svc.content.presets.values())
    if args.limit:
        presets = presets[: args.limit]
    team = select_team(svc.content, None, settings.team_size)
    by_name = {p["name"]: p for p in svc.content.personas.values()}
    print(f"models: {svc.llm.info()}  questions: {len(presets)}  arms: {arms}")
    results = []
    try:
        for q in presets:
            case = case_of(q)
            runs = {
                "A": lambda: arm_a(svc, case, team),
                "B": lambda: arm_b(svc, case, team, args.rounds),
                "C": lambda: arm_multi(svc, q["id"], False, args.rounds),
                "ours": lambda: arm_multi(svc, q["id"], True, args.rounds),
            }
            outs = await asyncio.gather(*(runs[a]() for a in arms), return_exceptions=True)
            row = {"id": q["id"], "title": q.get("title", q["id"]), "dilemma": q["dilemma"], "arms": dict(), "failed": dict()}
            for a, o in zip(arms, outs):
                if isinstance(o, BaseException):
                    row["failed"][a] = {"error": f"{type(o).__name__}: {o}"}
                    print(f"  {q['id']} / {a} FAILED: {o}")
                    continue
                m = await measure(svc, o["speeches"], by_name)
                m["unjustified_stance_changes"] = o["unjustified_stance_changes"]
                row["arms"][a] = {"metrics": m, "final": o["final"], "speeches": o["speeches"],
                    "session_id": o.get("session_id"), "rewrites": o.get("rewrites"), "llm": o.get("llm")}
                print(f"  {q['id']} / {a}: {m}")
            results.append(row)
            write_outputs(Path(args.out), results, arms, svc.llm.mock, args.seed)
    finally:
        await svc.shutdown()
    print(f"written to {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
