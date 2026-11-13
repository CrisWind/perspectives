"""安全兜底：识别用户输入里的危机信号，命中后不开辩论，直接给求助信息。

做法：每次输入都交给模型判断（实测只靠关键词会漏掉大量委婉表达，比如“每天都想着怎么结束这一切”“室友经常打我”）。
关键词只作为兜底：模型调用失败时，命中关键词按危机处理，没命中则放行。
模型明确判为危机或拿不准时，都展示求助信息。
"""
from __future__ import annotations

import logging
import re

from .llm import LLM, LLMError

log = logging.getLogger("perspectives.safety")

# 关键词只用来决定"要不要问模型"，命中不等于判定为危机
_SIGNALS = re.compile(
    r"自杀|轻生|寻死|寻短见|想死|去死|不如死|不想活|活不下去|活着没(?:有)?意思|活着(?:好)?累|结束(?:自己的)?生命|了结(?:自己|生命)"
    r"|自残|割腕|跳楼|跳河|上吊|烧炭|吞药|安眠药|一了百了|消失(?:在这个世界|算了)|解脱了|离开这个世界"
    r"|伤害自己|杀了自己|杀人|报复社会|家暴|被打|性侵|猥亵|强奸|被侵犯|虐待"
)

# 求助渠道。号码来自公开信息，上线前请再核对一次（尤其是各地区号和服务时间）。
HELP = {
    "message": "你说的这些我很在意。这件事比任何决策都重要，我不会让几个角色来辩论它。请先找一个真人聊一聊：",
    "lines": [
        {"name": "全国统一心理援助热线", "number": "12356", "note": "免费，覆盖焦虑、抑郁、危机干预"},
        {"name": "12355 青少年服务台", "number": "12355", "note": "共青团中央设立，24 小时，适合学生"},
        {"name": "紧急情况", "number": "120 / 110", "note": "如果你或身边的人现在就有危险，请立刻拨打"},
    ],
    "tips": [
        "如果你在学校，可以现在就联系辅导员、心理中心，或者一个信任的同学陪着你。",
        "先离开危险的物品或地点，和一个能陪你说话的人待在一起。",
    ],
}

_JUDGE_SYSTEM = """你是安全审核员。判断下面这段用户输入是否表达了真实的自伤、自杀、伤害他人，或正在遭受暴力、侵害、虐待的危机。
- crisis=true：有真实的自杀、自伤意念或计划，想伤害他人，或自己或他人正在遭受暴力或侵害；委婉、绝望的表达也算。
- crisis=false：夸张的口头禅（“累死了”“考不好我就去死”这类明显的玩笑或抱怨）、对别人事件的讨论、正常的人生选择。
委婉、间接的表达（“撑不下去”“结束这一切”“大家没有我会更好”“他经常打我”）要判 true。
拿不准时判 true。
只输出 JSON：{"crisis": true, "kind": "self_harm|harm_others|abuse|other", "reason": "一句话"}"""


def has_signal(*texts: str) -> bool:
    return any(t and _SIGNALS.search(t) for t in texts)


async def check(llm: LLM, texts: list[str], session_id: str | None = None) -> dict | None:
    """返回 None 表示没有问题；否则返回要展示给用户的求助信息。"""
    joined = chr(10).join(x for x in texts if x).strip()
    if not joined:
        return None
    signal = has_signal(joined)
    try:
        out, _ = await llm.json(
            "safety", _JUDGE_SYSTEM, joined[:1500], provider=llm.judge, session_id=session_id,
            temperature=0.0, max_tokens=150, mock={"text": joined, "signal": signal},
        )
    except LLMError as e:
        log.warning("safety judge failed (keyword hit: %s): %s", signal, e)
        return dict(HELP, kind="other") if signal else None
    if out.get("crisis") is not True:
        return None
    kind = out.get("kind") if out.get("kind") in ("self_harm", "harm_others", "abuse", "other") else "other"
    log.warning("crisis signal detected (session %s, kind %s)", session_id, kind)
    return dict(HELP, kind=kind)
