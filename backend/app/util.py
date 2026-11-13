from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str = "", n: int = 12) -> str:
    return prefix + uuid.uuid4().hex[:n]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """从模型输出里取出 JSON 对象。容忍代码块包裹和前后多余文字。"""
    if not text or not text.strip():
        raise ValueError("empty model output")
    t = text.strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in model output")
        obj = json.loads(t[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


def clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"
