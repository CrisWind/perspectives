"""快速模式预热：把每道预设题和各种 MBTI 对应的队伍提前跑好并缓存。

游园会现场回放缓存，一分钟内看完，不依赖模型当场响应。
换了角色、预设题、模型配置或上线了新的补充准则之后要重新跑。

用法（在 backend 目录下）：
  uv run python scripts/warm_presets.py    # 补齐还没缓存的
  uv run python scripts/warm_presets.py --clear   # 清空后全部重跑
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import engine  # noqa: E402
from app.config import Settings  # noqa: E402
from app.content import MBTI_TYPES, select_team  # noqa: E402
from app.engine import Services  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--parallel", type=int, default=2)
    args = ap.parse_args()
    svc = Services(Settings.from_env())
    if args.clear:
        print("cleared", svc.store.clear_preset_runs(), "cached runs")
    # 16 种 MBTI 对应的队伍有重复，每种队伍只需要跑一次
    teams: dict[tuple, str | None] = dict()
    for mbti in [None] + sorted(MBTI_TYPES):
        ids = tuple(p["id"] for p in select_team(svc.content, mbti, svc.settings.team_size))
        teams.setdefault(ids, mbti)
    jobs = [(pid, mbti) for pid in svc.content.presets for mbti in teams.values()]
    print(f"models: {svc.llm.info()}")
    print(f"{len(svc.content.presets)} presets x {len(teams)} teams = {len(jobs)} runs")
    sem = asyncio.Semaphore(args.parallel)
    failed = 0

    async def one(preset_id: str, mbti: str | None) -> None:
        nonlocal failed
        async with sem:
            s = await engine.create_session(svc, {"mode": "quick", "preset_id": preset_id, "mbti": mbti})
            svc.store.update_session(s["id"], status="running")
            await engine.Debate(svc, svc.store.get_session(s["id"])).run()
            status = svc.store.get_session(s["id"])["status"]
            failed += status != "completed"
            print(f"  {preset_id} / {mbti or '未填'}: {status}")

    try:
        await asyncio.gather(*(one(p, m) for p, m in jobs))
    finally:
        await svc.shutdown()
    print("done," if not failed else f"{failed} FAILED,", len(jobs) - failed, "ok")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
