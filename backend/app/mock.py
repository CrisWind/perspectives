"""内置模拟模型：没有密钥时用它跑通全流程。内容是模板拼出来的，只用于联调、测试和演示流程。

为了能看到质量引擎工作，模拟角色会故意犯两种错：
- 第 2 轮第一位角色第一次发言会照抄别人已有的论点（应被去重打回）。
- 第 2 轮第二位角色第一次发言会无理由改变立场（应被立场账本打回）。
"""
from __future__ import annotations

import re

from .util import clip

ANGLES = [
    ("时间成本", "这条路要占用你多少个月？这段时间原本可以换来什么？"),
    ("退路", "如果走不通，你回到原点需要多久、要付出什么？"),
    ("信息来源", "这个判断是你亲自核实过的，还是听别人说的？"),
    ("现金流", "接下来十二个月的收入和支出算过吗？缺口谁来补？"),
    ("同伴压力", "如果身边没有人这么选，你还会觉得它重要吗？"),
    ("三年后的简历", "三年后的面试官会怎么看这段经历？"),
    ("可逆性", "这一步做错了能改吗？改的代价和现在犹豫的代价哪个大？"),
    ("真实动机", "你是在奔向想要的东西，还是在逃离不喜欢的东西？"),
    ("最坏情况", "最糟的结果具体是什么样子？发生的可能有多大？"),
    ("替代方案", "有没有不用二选一也能拿到核心好处的做法？"),
    ("家人", "家里人的支持是有条件的吗？条件变了怎么办？"),
    ("精力", "你现在的身体和情绪状态，撑得住这个选择最难的那几个月吗？"),
]
POSITIONS = ["oppose", "conditional", "oppose", "support", "conditional"]
KEYWORDS = [
    ("study", "考研|读研|保研|留学|出国|升学|读博|硕士"),
    ("switch", "辞职|跳槽|转行|离职|裸辞"),
    ("city", "城市|回老家|老家|北上广|上海|北京|深圳|定居"),
    ("job", "offer|工作|秋招|春招|大厂|创业|实习|入职"),
]


def _category(text: str) -> str:
    for cat, pat in KEYWORDS:
        if re.search(pat, text, re.I):
            return cat
    return "other"


def _clarify(c: dict) -> dict:
    text = c.get("dilemma", "") + " " + c.get("leaning", "")
    reasons = [r for r in c.get("reasons") or [] if r.strip()]
    if not reasons:
        parts = re.split(r"[。；;！!？?\n]|因为|而且|另外", c.get("dilemma", ""))
        reasons = [p.strip(" ，,") for p in parts if len(p.strip(" ，,")) >= 6][:4]
    if not reasons:
        reasons = [clip(c.get("dilemma", "这个决定对我很重要"), 40)]
    questions = []
    if not c.get("facts"):
        questions.append("如果按现在的倾向去做，最坏的情况下你有什么退路？")
        questions.append("这个决定有截止时间吗？")
    return {
        "category": _category(text),
        "leaning": c.get("leaning") or "还没有明确倾向",
        "reasons": reasons,
        "questions": questions[: c.get("max_questions", 2)],
    }


def _speak(c: dict) -> dict:
    p, fw = c["persona"], c["framework"]
    idx, rnd, attempt = c["index"], c["round"], c.get("attempt", 0)
    reasons, pool = c["reasons"], c.get("pool") or []
    mine = [x for x in pool if x["persona_id"] == p["id"]]
    others = [x for x in pool if x["persona_id"] not in (p["id"], "user")]
    n = len(mine)
    claims = []

    def angle_claim(k: int, reason: dict) -> dict:
        name, q = ANGLES[(idx * 5 + n + k + attempt * 7) % len(ANGLES)]
        return {
            "type": "new", "text": f"从{name}看「{clip(reason['text'], 18)}」：{q}",
            "reason_id": reason["id"], "targets": None, "direction": "challenge",
        }

    focus = c.get("focus") or reasons[(idx + rnd) % len(reasons)]
    if rnd == 1:
        r0 = reasons[idx % len(reasons)]
        move = fw["moves"][0]
        claims.append({
            "type": "new", "text": f"用{fw['name']}检验「{clip(r0['text'], 18)}」：{move}",
            "reason_id": r0["id"], "targets": None, "direction": "challenge",
        })
        claims.append(angle_claim(0, reasons[(idx + 1) % len(reasons)]))
    else:
        if idx == 0 and rnd == 2 and attempt == 0 and others:
            src = others[0]
            claims.append({
                "type": "new", "text": src["text"], "reason_id": src.get("reason_id"),
                "targets": None, "direction": "challenge",
            })
        elif others:
            t = others[(idx + rnd + attempt) % len(others)]
            name, q = ANGLES[(idx * 5 + n + 3 + attempt * 7) % len(ANGLES)]
            claims.append({
                "type": "rebuttal" if (idx + rnd) % 2 == 0 else "evidence",
                "text": f"回应 {t['id']}：只看这一点不够，还要补上{name}——{q}",
                "reason_id": t.get("reason_id") or focus["id"], "targets": t["id"],
                "direction": "challenge" if idx % 3 else "neutral",
            })
        claims.append(angle_claim(1, focus))
    user_claims = c.get("user_claims") or []
    if user_claims and rnd > 1:
        u = user_claims[-1]
        claims[0] = {
            "type": "rebuttal", "text": f"你说「{clip(u['text'], 20)}」，{p['name']}想追问：{fw['moves'][-1]}（第{rnd}轮）",
            "reason_id": u.get("reason_id"), "targets": u["id"], "direction": "challenge",
        }

    prev = c.get("prev_stance")
    position = prev["position"] if prev else POSITIONS[idx % 5]
    persuaded = None
    note = f"{p['name']}按{fw['name']}的判断"
    if idx == 1 and rnd == 2 and prev:
        position = "support" if prev["position"] != "support" else "conditional"
        if attempt > 0 and others:
            persuaded = others[0]["id"]
            note = f"被 {persuaded} 说服，调整了看法"
        else:
            note = "想了想，改主意了"
    speech = f"（{p['name']}）" + "；".join(x["text"] for x in claims)
    return {
        "speech": clip(speech, 300), "claims": claims,
        "stance": {"position": position, "confidence": 0.6, "persuaded_by": persuaded, "note": note},
    }


def _card(c: dict) -> dict:
    pool = [x for x in c.get("pool") or [] if x["persona_id"] != "user"]
    reasons = c["reasons"]
    checks = []
    for i, r in enumerate(reasons):
        ids = [x["id"] for x in pool if x.get("reason_id") == r["id"]][:4]
        verdict = ["shaky", "weak", "solid"][i % 3] if ids else "shaky"
        checks.append({
            "reason_id": r["id"], "verdict": verdict,
            "summary": f"围绕这条理由有 {len(ids)} 条检验，主要的疑问还没有被回答。" if ids else "这条理由没有被充分讨论。",
            "claim_ids": ids,
        })
    blind = [{"text": x["text"], "claim_ids": [x["id"]]} for x in pool if x["type"] == "new"][-3:]
    return {
        "reason_checks": checks,
        "blind_spots": blind,
        "core_tradeoff": {"a": "眼前的确定性", "b": "长期的可能性", "summary": "说到底，你是在眼前的确定性和长期的可能性之间做选择。"},
        "recommendations": [
            {"condition": "如果你更看重长期的可能性，并且退路已经想清楚", "suggestion": f"倾向按现在的想法去做：{clip(c.get('leaning', ''), 20)}"},
            {"condition": "如果最坏情况你承受不了，或者关键事实还没核实", "suggestion": "先不要动，把疑问最大的那条理由核实清楚再决定"},
        ],
        "next_steps": ["把疑问最大的那条理由写下来，找一个真正经历过的人问一次", "给自己定一个最晚做决定的日期"],
        "values": ["成长", "不落后于同龄人"],
    }


def _judge(c: dict) -> dict:
    return {
        "claims": [{"index": i, "duplicate_of": None} for i in range(len(c.get("claims") or []))],
        "persona": {"consistent": True, "issue": ""},
    }


def _baseline_b(c: dict) -> dict:
    reasons = c["reasons"]
    turns = []
    for rnd in range(1, c.get("rounds", 3) + 1):
        for i, name in enumerate(c["names"]):
            r = reasons[i % len(reasons)]
            turns.append({
                "round": rnd, "persona": name,
                "speech": f"我觉得「{clip(r['text'], 18)}」需要再想想，风险和机会都要考虑。",
                "position": "conditional" if rnd < 3 else "support", "persuaded_by": None,
            })
    return {"turns": turns, "conclusion": "综合来看，按你的想法去做是可以的，但要注意风险。"}


def _extract(c: dict) -> dict:
    parts = re.split(r"[。；;！!？?\n]", c.get("text", ""))
    rows = [p.strip() for p in parts if len(p.strip()) >= 8][:12]
    return {"claims": [{"text": r, "challenge": "不" in r or "吗" in r or "多久" in r} for r in rows]}


def _cluster(c: dict) -> dict:
    return {"groups": [[i] for i in range(len(c.get("claims") or []))]}


def mock_response(purpose: str, c: dict) -> dict:
    if purpose == "clarify":
        return _clarify(c)
    if purpose == "speak":
        return _speak(c)
    if purpose == "judge":
        return _judge(c)
    if purpose == "card":
        return _card(c)
    if purpose == "queries":
        return {"queries": [clip(c.get("dilemma", ""), 30)]}
    if purpose == "generalize":
        return {"pattern": "追问方式：" + re.sub(r"「.*?」", "这条理由", c.get("text", ""))}
    if purpose == "patch":
        return {"patch": "追问时落到具体的数字、日期和人，不讲原则性的话。", "rationale": "被标为没用的论点大多过于笼统。"}
    if purpose == "baseline_a":
        return {"text": f"关于你的问题，我的看法是：你的想法有道理。{clip(c.get('leaning', ''), 20)}是可以的。不过也要考虑风险。建议你综合考虑后做决定。"}
    if purpose == "baseline_b":
        return _baseline_b(c)
    if purpose == "extract":
        return _extract(c)
    if purpose == "cluster":
        return _cluster(c)
    if purpose == "safety":
        return dict(crisis=bool(c.get("signal")), kind="self_harm")
    if purpose == "lean":
        return dict(lean="conditional")
    if purpose == "persona_score":
        return {"consistent": True}
    return dict()
