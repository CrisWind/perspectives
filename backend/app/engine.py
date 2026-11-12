"""辩论编排：澄清 → 组队 → 第一轮独立作答 → 交叉质询 → 决策卡；支持插话、会后异议、快速模式缓存。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

from . import evolution, prompts, quality
from .config import Settings
from .content import Content, norm_mbti, select_team
from .llm import LLM, LLMError
from .search import Search
from .store import Store
from .util import clip, new_id

log = logging.getLogger("perspectives.engine")

_ID_REF = re.compile(r" ?(?<![A-Za-z0-9])([rcRC])(\d{1,3})(?![A-Za-z0-9])\s?")
VERDICTS = ("solid", "shaky", "weak")
SLOW_EVENTS = ("utterance.completed", "quality.rejected", "card.ready")


class Services:
    """后端各部件的容器，也负责事件广播和后台任务。"""

    def __init__(self, settings: Settings, store: Store | None = None, content: Content | None = None,
                        llm: LLM | None = None, search: Search | None = None):
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.content = content or Content()
        self.llm = llm or LLM(settings, self.store)
        self.search = search or Search(settings)
        self.tasks: dict[str, asyncio.Task] = dict()
        self.pending: dict[str, list[dict]] = dict()  # 辩论进行中用户的插话，等下一轮处理
        self._waiters: dict[str, asyncio.Event] = dict()

    def emit(self, sid: str, type_: str, data: dict) -> dict:
        ev = self.store.append_event(sid, type_, data)
        w = self._waiters.get(sid)
        if w:
            w.set()
        return ev

    async def wait(self, sid: str, timeout: float) -> None:
        w = self._waiters.setdefault(sid, asyncio.Event())
        w.clear()
        try:
            await asyncio.wait_for(w.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    def running(self, sid: str) -> bool:
        t = self.tasks.get(sid)
        return bool(t) and not t.done()

    def spawn(self, sid: str, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks[sid] = task

        def _done(t: asyncio.Task) -> None:
            if self.tasks.get(sid) is t:
                self.tasks.pop(sid, None)
            if not t.cancelled() and t.exception():
                log.error("session %s task crashed: %r", sid, t.exception())

        task.add_done_callback(_done)

    async def shutdown(self) -> None:
        for t in list(self.tasks.values()):
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.llm.close()
        self.store.close()


# ---------- 创建与澄清 ----------
def _reason_list(texts: list[str]) -> list[dict]:
    clean = [clip(t, 200) for t in texts if isinstance(t, str) and t.strip()]
    return [{"id": f"r{i + 1}", "text": t} for i, t in enumerate(clean[:6])]


async def create_session(svc: Services, req: dict) -> dict:
    """req: dilemma, leaning, reasons, facts, mbti, user_id, mode, preset_id, options。"""
    st = svc.settings
    mode = req.get("mode") if req.get("mode") in ("quick", "deep", "eval") else "deep"
    opts = dict(req.get("options") or dict())
    opts.setdefault("quality_engine", True)
    sid = new_id("s_")
    user_id = req.get("user_id") or None
    mbti = norm_mbti(req.get("mbti"))
    if user_id and not mbti:
        mbti = (svc.store.get_profile(user_id) or dict()).get("mbti")
    preset = svc.content.presets.get(req.get("preset_id") or "")
    if req.get("preset_id") and not preset:
        raise ValueError("unknown preset_id")
    base = {"id": sid, "user_id": user_id, "mode": mode, "mbti": mbti, "options": opts,
            "preset_id": preset["id"] if preset else None}
    if preset:
        base.update(
            dilemma=preset["dilemma"], leaning=preset["leaning"], category=preset["category"],
            reasons=_reason_list(preset["reasons"]), facts=list(preset.get("facts") or []),
            questions=[], status="ready",
        )
    else:
        dilemma = (req.get("dilemma") or "").strip()
        if len(dilemma) < 4:
            raise ValueError("dilemma is required")
        given = [r for r in req.get("reasons") or [] if isinstance(r, str) and r.strip()]
        facts = [clip(f, 200) for f in req.get("facts") or [] if isinstance(f, str) and f.strip()]
        max_q = 0 if mode != "deep" or opts.get("skip_questions") else 2
        system, user = prompts.clarify(dilemma, req.get("leaning") or "", given, facts, max_q)
        try:
            out, _ = await svc.llm.json(
                "clarify", system, user, provider=svc.llm.judge, session_id=sid, temperature=0.2, max_tokens=700,
                mock={"dilemma": dilemma, "leaning": req.get("leaning") or "", "reasons": given,
        "facts": facts, "max_questions": max_q},
            )
        except LLMError as e:
            log.warning("clarify failed, using raw input: %s", e)
            out = dict()
        reasons = _reason_list(out.get("reasons") if isinstance(out.get("reasons"), list) else []) \
            or _reason_list(given) or _reason_list([dilemma])
        qs = [q for q in (out.get("questions") or []) if isinstance(q, str) and q.strip()][:max_q]
        cat = out.get("category")
        base.update(
            dilemma=clip(dilemma, 1000),
            leaning=clip(str(out.get("leaning") or req.get("leaning") or ""), 200),
            category=cat if cat in ("study", "job", "switch", "city", "other") else "other",
            reasons=reasons, facts=facts,
            questions=[{"id": f"q{i + 1}", "text": q, "answer": None} for i, q in enumerate(qs)],
            status="clarifying" if qs else "ready",
        )
    session = svc.store.create_session(base)
    if user_id and mbti:
        row = svc.store.get_profile(user_id)
        svc.store.save_profile(user_id, mbti, (row or dict()).get("profile") or evolution.load_profile(svc.store, None))
    svc.emit(sid, "status", {"status": session["status"]})
    return session


def answer_clarify(svc: Services, sid: str, answers: list[dict], reasons: list[str] | None = None) -> dict:
    s = svc.store.get_session(sid)
    if s["status"] not in ("clarifying", "ready"):
        raise ValueError("session already started")
    by_id = {a.get("id"): a.get("answer") for a in answers or [] if isinstance(a, dict)}
    for q in s["questions"]:
        if isinstance(by_id.get(q["id"]), str) and by_id[q["id"]].strip():
            q["answer"] = clip(by_id[q["id"]], 300)
    fields = {"questions": s["questions"], "status": "ready"}
    if reasons:
        new = _reason_list(reasons)
        if new:
            fields["reasons"] = new
    svc.store.update_session(sid, **fields)
    svc.emit(sid, "status", {"status": "ready"})
    return svc.store.get_session(sid)


def start(svc: Services, sid: str) -> None:
    s = svc.store.get_session(sid)
    if svc.running(sid) or s["status"] not in ("ready", "clarifying"):
        raise ValueError("session cannot be started in status " + s["status"])
    svc.store.update_session(sid, status="running")
    svc.spawn(sid, Debate(svc, svc.store.get_session(sid)).run())


async def run_headless(svc: Services, preset_id: str, options: dict, mode: str = "eval",
                case: dict | None = None) -> str:
    """不经过接口直接跑完一场（评测和提示词门槛用）。返回会话编号。"""
    req = {"mode": mode, "options": options, "user_id": None}
    if case:
        req.update(case)
        req["options"] = dict(options, skip_questions=True)
    else:
        req["preset_id"] = preset_id
    s = await create_session(svc, req)
    svc.store.update_session(s["id"], status="running")
    await Debate(svc, svc.store.get_session(s["id"])).run()
    return s["id"]


def interject(svc: Services, sid: str, text: str, reason_id: str | None = None, claim_id: str | None = None) -> dict:
    """用户插话。辩论进行中：排队，下一轮角色必须回应。辩论结束后：开一轮回应并更新决策卡。"""
    s = svc.store.get_session(sid)
    text = clip(text or "", 400)
    if len(text) < 2:
        raise ValueError("text is required")
    item = {"text": text, "reason_id": reason_id, "claim_id": claim_id}
    if s["status"] == "running" and svc.running(sid) and not s.get("card"):
        svc.pending.setdefault(sid, []).append(item)
        return {"queued": True}
    if s["status"] != "completed":
        raise ValueError("session is not running or completed")
    if svc.running(sid):
        raise ValueError("a follow-up is already in progress")
    svc.store.update_session(sid, status="running")
    svc.spawn(sid, Debate(svc, svc.store.get_session(sid)).followup([item]))
    return {"queued": False, "followup": True}


# ---------- 一场辩论 ----------
class Debate:
    def __init__(self, svc: Services, session: dict):
        self.svc, self.s, self.sid = svc, session, session["id"]
        st = svc.settings
        self.opts = session.get("options") or dict()
        self.engine_on = bool(self.opts.get("quality_engine", True))
        default_rounds = st.quick_rounds if session["mode"] == "quick" else st.deep_rounds
        self.rounds = max(1, min(5, int(self.opts.get("rounds") or default_rounds)))
        self.team = select_team(svc.content, session.get("mbti"), st.team_size, self.opts.get("team"))
        self.names = {p["id"]: p["name"] for p in self.team}
        self.provider = {p["id"]: svc.llm.persona_provider(i) for i, p in enumerate(self.team)}
        self.index = {p["id"]: i for i, p in enumerate(self.team)}
        # 续跑（会后异议）时从库里恢复状态
        self.pool: list[dict] = svc.store.claims(self.sid)
        self.stance: dict[str, dict] = dict()
        for row in svc.store.stances(self.sid):
            self.stance[row["persona_id"]] = row
        utts = svc.store.utterances(self.sid)
        self.n_claim = max([int(c["id"][1:]) for c in self.pool], default=0)
        self.n_utt = max([int(u["id"][1:]) for u in utts], default=0)
        self.round = max([u["round"] for u in utts], default=0)
        self.patches: dict[str, dict | None] = dict()
        overrides = self.opts.get("patch_overrides") or dict()
        for p in self.team:
            if p["id"] in overrides:
                self.patches[p["id"]] = svc.store.get_prompt_version(int(overrides[p["id"]]))
            else:
                self.patches[p["id"]] = svc.store.patch_for(p["id"])
        self.experiences: dict[str, list[dict]] = dict()
        self.search_results: list[dict] = []
        self.profile_note = evolution.profile_note(svc.store, session.get("user_id"), self.sid)
        self.record: list[dict] = []

    # ----- 基础 -----
    def emit(self, type_: str, data: dict) -> None:
        self.svc.emit(self.sid, type_, data)
        self.record.append({"type": type_, "data": data})

    def public_team(self) -> list[dict]:
        return [self.svc.content.public_persona(p, self.provider[p["id"]]) for p in self.team]

    def persona_claims(self) -> list[dict]:
        return [c for c in self.pool if c["persona_id"] != "user"]

    def cache_key(self) -> str:
        parts = [
            self.s.get("preset_id") or "", ",".join(p["id"] for p in self.team), self.svc.content.version,
            str(self.rounds), ",".join(self.svc.llm.persona_pool),
            ",".join(f"{k}:{v['id']}" for k, v in sorted(self.patches.items()) if v),
        ]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]

    # ----- 主流程 -----
    async def run(self) -> None:
        try:
            self.emit("status", {"status": "running"})
            cacheable = (
                self.s["mode"] == "quick" and self.s.get("preset_id") and self.engine_on
                and self.opts.get("use_cache", True)
            )
            cached = self.svc.store.get_preset_run(self.cache_key()) if cacheable else None
            if cached:
                await self._replay(cached)
            else:
                await self._live()
                if cacheable:
                    self.svc.store.save_preset_run(self.cache_key(), self.s["preset_id"], self.record)
            await self._after()
        except asyncio.CancelledError:
            self.svc.emit(self.sid, "status", {"status": "failed"})
            raise
        except Exception as e:  # noqa: BLE001 任何错误都要让前端知道，而不是卡在"进行中"
            log.exception("debate %s failed", self.sid)
            self.svc.emit(self.sid, "error", {"message": clip(str(e), 300)})
            self.svc.emit(self.sid, "status", {"status": "failed"})
            self.svc.emit(self.sid, "done", {"status": "failed"})

    async def _replay(self, events: list[dict]) -> None:
        """快速模式：回放预先生成好的辩论，不依赖模型实时响应。"""
        delay = self.svc.settings.quick_replay_delay
        for ev in events:
            if ev["type"] in ("status", "done"):
                continue
            if delay and ev["type"] in SLOW_EVENTS:
                await asyncio.sleep(delay)
            self.svc.emit(self.sid, ev["type"], ev["data"])
        self.svc.emit(self.sid, "status", {"status": "completed"})
        self.svc.emit(self.sid, "done", {"status": "completed", "cached": True})

    async def _live(self) -> None:
        self.emit("team.selected", {"team": self.public_team(), "quality_engine": self.engine_on})
        self._load_experiences()
        await self._search()
        await self._round_independent()
        for _ in range(self.rounds - 1):
            await self._round_cross()
        await self._challenge_floor()
        await self._card("card.ready")
        self.svc.emit(self.sid, "status", {"status": "completed"})
        self.svc.emit(self.sid, "done", {"status": "completed"})

    async def _after(self) -> None:
        s = self.svc.store.get_session(self.sid)
        if s.get("card"):
            evolution.on_completed(self.svc, s, s["card"])
        waiting = self.svc.pending.pop(self.sid, None)
        if waiting:  # 最后一轮开始后才到的插话，会后补一轮
            self.svc.store.update_session(self.sid, status="running")
            await Debate(self.svc, self.svc.store.get_session(self.sid)).followup(waiting)

    async def followup(self, items: list[dict]) -> None:
        """用户看完决策卡后不同意：相关角色回应，然后更新决策卡。"""
        try:
            self.emit("status", {"status": "running"})
            user_claims = self._add_user_claims(items)
            self.round += 1
            self.emit("round.started", {"round": self.round, "kind": "followup"})
            owners = []
            for it in items:
                target = next((c for c in self.pool if c["id"] == it.get("claim_id")), None)
                if target and target["persona_id"] in self.index:
                    owners.append(target["persona_id"])
            future = self.team[-1]["id"]
            order = list(dict.fromkeys(owners + [self.team[0]["id"], future]))[:3]
            await self._wave([self._job(self.team[self.index[pid]], "followup", None, user_claims) for pid in order])
            self.emit("round.completed", {"round": self.round})
            await self._card("card.updated")
            self.svc.emit(self.sid, "status", {"status": "completed"})
            self.svc.emit(self.sid, "done", {"status": "completed"})
        except asyncio.CancelledError:
            self.svc.emit(self.sid, "status", {"status": "completed"})
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("followup %s failed", self.sid)
            self.svc.emit(self.sid, "error", {"message": clip(str(e), 300)})
            self.svc.emit(self.sid, "status", {"status": "completed"})  # 原来的决策卡仍然有效
            self.svc.emit(self.sid, "done", {"status": "completed"})

    # ----- 准备 -----
    def _load_experiences(self) -> None:
        if not self.opts.get("use_experience", True):
            return
        items = []
        for p in self.team:
            picked = evolution.pick_experiences(self.svc.store, self.s.get("category"), p)
            self.experiences[p["id"]] = picked
            for e in picked:
                self.svc.store.bump_experience(e["id"], uses=1)
                items.append({"id": e["id"], "pattern": e["pattern"], "persona_id": p["id"]})
        if items:
            self.emit("experience.applied", {"items": items})

    async def _search(self) -> None:
        searcher = next((p for p in self.team if p.get("can_search")), None)
        if not searcher or not self.svc.search.enabled:
            return
        system, user = prompts.queries(self.s)
        try:
            out, _ = await self.svc.llm.json("queries", system, user, provider=self.svc.llm.judge,
                        session_id=self.sid, temperature=0.2, max_tokens=200,
                        mock={"dilemma": self.s["dilemma"]})
        except LLMError:
            return
        qs = [q for q in out.get("queries") or [] if isinstance(q, str) and q.strip()][:2]
        found = await asyncio.gather(*(self.svc.search.search(q) for q in qs))
        seen = set()
        for rows in found:
            for r in rows:
                if r["url"] not in seen:
                    seen.add(r["url"])
                    self.search_results.append(r)
        self.emit("search.done", {"persona_id": searcher["id"], "queries": qs, "results": self.search_results})

    def _add_user_claims(self, items: list[dict]) -> list[dict]:
        out = []
        reason_ids = {r["id"] for r in self.s["reasons"]}
        for it in items:
            self.n_claim += 1
            target = it.get("claim_id") if any(c["id"] == it.get("claim_id") for c in self.pool) else None
            claim = {
                "id": f"c{self.n_claim}", "round": self.round, "persona_id": "user", "type": "user",
                "text": it["text"], "reason_id": it.get("reason_id") if it.get("reason_id") in reason_ids else None,
                "targets": target, "direction": "neutral", "sources": [], "echoed_by": [],
            }
            self.pool.append(claim)
            out.append(claim)
            self.emit("user.interjected", {"claim": claim})
        return out

    # ----- 各轮 -----
    def _job(self, p: dict, kind: str, focus: dict | None = None, user_claims: list[dict] | None = None,
        need_challenge: bool = False) -> dict:
        return {
            "p": p, "kind": kind, "focus": focus, "user_claims": user_claims or [],
            "need_challenge": need_challenge, "rejection": None, "cand": None, "attempts": 0,
            "norm": None, "issues": [], "stance_issue": None,
        }

    async def _round_independent(self) -> None:
        self.round = 1
        self.emit("round.started", {"round": 1, "kind": "independent"})
        await self._wave([self._job(p, "independent") for p in self.team])
        self.emit("round.completed", {"round": 1})

    async def _round_cross(self) -> None:
        self.round += 1
        waiting = self.svc.pending.pop(self.sid, None) or []
        self.emit("round.started", {"round": self.round, "kind": "cross"})
        user_claims = self._add_user_claims(waiting) if waiting else []
        shift = (self.round - 2) % len(self.team)
        order = self.team[shift:] + self.team[:shift]
        counts = {r["id"]: sum(1 for c in self.pool if c.get("reason_id") == r["id"]) for r in self.s["reasons"]}
        jobs = []
        for p in order:
            focus = min(self.s["reasons"], key=lambda r: counts[r["id"]])
            counts[focus["id"]] += 2
            jobs.append(self._job(p, "cross", focus, user_claims))
        # 同一批的角色同时发言，看到的是上一批结束时的论点池；批内撞车由质检拦下后重写
        size = self.svc.settings.cross_parallel or len(jobs)
        for i in range(0, len(jobs), size):
            await self._wave(jobs[i : i + size])
        self.emit("round.completed", {"round": self.round})

    async def _challenge_floor(self) -> None:
        """防讨好的底线：针对用户倾向的挑战不够数，就点名让角色补上。"""
        need = self.svc.settings.min_challenges
        have = sum(1 for c in self.persona_claims() if c["direction"] == "challenge")
        if not self.engine_on or have >= need:
            return
        self.round += 1
        self.emit("round.started", {"round": self.round, "kind": "challenge", "have": have, "need": need})

        def rank(p: dict) -> tuple:
            return (self.stance.get(p["id"], dict()).get("position") == "support", self.index[p["id"]])

        ranked = sorted(self.team, key=rank)
        await self._wave([self._job(p, "cross", need_challenge=True) for p in ranked[:2]])
        self.emit("round.completed", {"round": self.round})

    # ----- 发言 -----
    async def _generate(self, p: dict, kind: str, focus: dict | None, user_claims: list[dict],
        need_challenge: bool, rejection: list[dict] | None, attempt: int) -> dict:
        svc = self.svc
        fw = svc.content.framework_of(p)
        system = prompts.persona_system(p, fw, svc.settings.speech_limit, self.patches.get(p["id"]), svc.search.enabled)
        visible = [] if kind == "independent" else list(self.pool)
        user = prompts.persona_user(
            self.s, rnd=self.round, kind=kind, pool=visible, names=self.names,
            prev_stance=self.stance.get(p["id"]), focus=focus,
            experiences=self.experiences.get(p["id"], []), profile_note=self.profile_note,
            search_results=self.search_results if p.get("can_search") else [],
            user_claims=user_claims, need_challenge=need_challenge, rejection=rejection,
        )
        out, _ = await svc.llm.json(
            "speak", system, user, provider=self.provider[p["id"]], session_id=self.sid,
            temperature=0.8 if attempt == 0 else 0.9, max_tokens=900,
            mock={
                "persona": p, "framework": fw, "index": self.index[p["id"]], "round": self.round, "kind": kind,
                "attempt": attempt, "reasons": self.s["reasons"], "pool": visible, "focus": focus,
                "prev_stance": self.stance.get(p["id"]), "user_claims": user_claims,
            },
        )
        return out

    async def _wave(self, jobs: list[dict]) -> None:
        """一批角色同时发言：并行生成、并行质检，再按顺序逐个入池。没过的一起重写，最多重写 max_retries 次。

        并行是提速的关键：一轮的耗时从"每个角色的生成加质检之和"降到"最慢的那一个"。
        """
        st = self.svc.settings
        for j in jobs:
            self.emit("utterance.started", {"persona_id": j["p"]["id"], "round": self.round})
        allowed = {r["url"] for r in self.search_results}
        retries = st.max_retries if self.engine_on else 0
        pending = list(jobs)
        for attempt in range(retries + 1):
            if not pending:
                break
            outs = await asyncio.gather(
                *(
                    self._generate(j["p"], j["kind"], j["focus"], j["user_claims"], j["need_challenge"],
                        j["rejection"], attempt)
                    for j in pending
                ),
                return_exceptions=True,
            )
            pool_ids = {c["id"] for c in self.pool}
            for j, out in zip(pending, outs):
                j["attempts"] = attempt + 1
                j["norm"], j["issues"], j["stance_issue"] = None, [], None
                if isinstance(out, BaseException):
                    if not isinstance(out, Exception):
                        raise out
                    log.warning("generation failed for %s: %s", j["p"]["id"], out)
                    continue
                norm, issues = quality.normalize(out, self.s["reasons"], pool_ids, st.speech_limit)
                j["norm"], j["issues"] = norm, issues
                if norm is not None:
                    quality.check_sources(norm["claims"], allowed)  # 假来源直接去掉，不值得重写
                    j["stance_issue"] = quality.check_stance(
                        norm["stance"], self.stance.get(j["p"]["id"]), j["p"]["id"], self.pool
                    )
            verdicts = await asyncio.gather(*(self._prejudge(pending, k) for k in range(len(pending))))
            temp: dict[str, str] = dict()  # 本批里的临时编号 -> 入池后的正式编号
            still = []
            for k, (j, verdict) in enumerate(zip(pending, verdicts)):
                if j["norm"] is None:
                    if j["issues"]:
                        j["rejection"] = j["issues"]
                        if self.engine_on:
                            self.emit("quality.rejected", {
    "persona_id": j["p"]["id"], "round": self.round, "attempt": j["attempts"],
    "issues": j["issues"],
                            })
                    still.append(j)
                elif self._evaluate(j, verdict, temp):
                    self._commit(j, True, temp, k)
                else:
                    still.append(j)
            pending = still
        for j in pending:
            self._commit(j, False)

    async def _prejudge(self, pending: list[dict], k: int) -> tuple:
        """裁判模型检查第 k 个角色的发言。同批里排在前面的发言用临时编号（t0_1 这种）一起放进论点池供比对。"""
        j = pending[k]
        if not self.engine_on or j["norm"] is None:
            return dict(), None, None
        pool = [c for c in self.pool if c["persona_id"] != "user"]
        for m in range(k):
            other = pending[m]["norm"]
            if other is not None:
                pool += [{"id": f"t{m}_{i}", "text": c["text"]} for i, c in enumerate(other["claims"])]
        p = j["p"]
        return await quality.judge(
            self.svc.llm, self.sid, p, self.svc.content.framework_of(p), j["norm"]["speech"], j["norm"]["claims"],
            pool, j["norm"]["stance"]["position"],
        )

    def _evaluate(self, j: dict, verdict: tuple, temp: dict[str, str]) -> bool:
        """汇总一条发言的全部质检结果。通过返回 True；不通过则记下打回原因并返回 False。"""
        st = self.svc.settings
        norm, pid, kind = j["norm"], j["p"]["id"], j["kind"]
        issues = list(j["issues"])
        dups: dict[int, str] = dict()
        persona_issue = mismatch = None
        if self.engine_on:
            for i, c in enumerate(norm["claims"]):
                # 只和角色的论点比：回应用户插话时引用用户原话不算重复
                best, score = quality.most_similar(c["text"], self.persona_claims())
                if best is not None and score >= st.dup_hard:
                    dups[i] = best["id"]
            jd, persona_issue, mismatch = verdict
            for i, d in jd.items():
                d = temp.get(d, d)
                if not d.startswith("t"):  # 仍是临时编号，说明对方那条发言没有入池，不算重复
                    dups.setdefault(i, d)
            if kind != "independent":  # 第一轮互相看不到，撞车算"不谋而合"，不算重复
                for i, d in sorted(dups.items()):
                    issues.append({
                        "code": "duplicate", "similar_to": d, "claim_text": norm["claims"][i]["text"],
                        "message": f"和已有论点 {d}「{clip(self._claim_text(d), 60)}」重复，换一个池子里没有的角度，或者直接反驳某条论点",
                    })
            if persona_issue:
                issues.append({"code": "persona", "message": "偏离人设：" + persona_issue})
            if j["stance_issue"]:
                issues.append(j["stance_issue"])
            if mismatch:
                issues.append({"code": "stance_mismatch", "message": "说一套、站一套：" + mismatch})
            fresh = [c for i, c in enumerate(norm["claims"]) if i not in dups]
            if j["need_challenge"] and not any(c["direction"] == "challenge" for c in fresh):
                issues.append({"code": "no_challenge", "message": "没有提出针对用户倾向的挑战"})
        this = {
            "norm": norm, "dups": dups, "persona_issue": persona_issue, "stance_issue": j["stance_issue"],
            "mismatch": mismatch, "fresh": len(norm["claims"]) - len(dups), "attempts": j["attempts"],
        }
        if not self.engine_on or not issues:
            j["cand"] = this
            return True
        if j["cand"] is None or this["fresh"] >= j["cand"]["fresh"]:  # 降级时用新论点最多的那一次
            j["cand"] = this
        j["rejection"] = issues
        self.emit("quality.rejected", {
            "persona_id": pid, "round": self.round, "attempt": j["attempts"], "issues": issues,
            "speech": clip(norm["speech"], 200),
        })
        return False

    def _commit(self, j: dict, accepted: bool, temp: dict[str, str] | None = None, k: int = 0) -> None:
        """发言入池并通知前端。accepted 为 False 表示重写次数用完后的降级处理。"""
        st = self.svc.settings
        pid, kind, cand, focus = j["p"]["id"], j["kind"], j["cand"], j["focus"]
        if cand is None:
            self.emit("utterance.dropped", {"persona_id": pid, "round": self.round, "reason": "多次生成失败"})
            return
        norm, dups = cand["norm"], cand["dups"] if self.engine_on else dict()
        prev = self.stance.get(pid)
        degraded = not accepted
        if degraded and cand["stance_issue"] and prev:
            quality.revert_stance(norm["stance"], prev)
        echoes = [{"claim_id": d, "text": norm["claims"][i]["text"]} for i, d in sorted(dups.items())]
        if len(dups) == len(norm["claims"]) and kind != "independent":
            self.emit("utterance.dropped", {"persona_id": pid, "round": self.round, "reason": "重写后仍然没有新论点"})
            return
        norm["speech"] = self._humanize(norm["speech"], pid)
        self.n_utt += 1
        claims = []
        for i, c in enumerate(norm["claims"]):
            if i in dups:
                if temp is not None:
                    temp[f"t{k}_{i}"] = dups[i]
                continue
            self.n_claim += 1
            cid = f"c{self.n_claim}"
            if temp is not None:
                temp[f"t{k}_{i}"] = cid
            text = self._humanize(c["text"], pid)
            claims.append(dict(c, text=text, id=cid, round=self.round, persona_id=pid, echoed_by=[]))
        self.pool.extend(claims)
        for e in echoes:
            src = next((c for c in self.pool if c["id"] == e["claim_id"]), None)
            if src is not None and pid not in src.setdefault("echoed_by", []):
                src["echoed_by"].append(pid)
        norm["stance"]["note"] = self._humanize(norm["stance"]["note"], pid)
        self.stance[pid] = norm["stance"]
        self.emit("utterance.completed", {
            "id": f"u{self.n_utt}", "round": self.round, "kind": kind, "persona_id": pid,
            "speech": clip(norm["speech"], int(st.speech_limit * 1.6)), "claims": claims, "echoes": echoes,
            "stance": norm["stance"], "attempts": cand["attempts"], "degraded": degraded,
            "persona_flag": bool(degraded and cand["persona_issue"]),
            "stance_mismatch": bool(degraded and cand["mismatch"]), "provider": self.provider[pid],
            "focus_reason_id": focus["id"] if focus else None,
        })

    def _claim_text(self, cid: str) -> str:
        return next((c["text"] for c in self.pool if c["id"] == cid), "")

    def _humanize(self, text: str, speaker: str) -> str:
        """把 r1、c3 这类内部编号换成用户看得懂的说法。"""
        reasons = {r["id"]: r["text"] for r in self.s["reasons"]}
        owners = {c["id"]: c["persona_id"] for c in self.pool}

        def sub(m: re.Match) -> str:
            key = m.group(1).lower() + m.group(2)
            if key in reasons:
                if text[m.end():m.end() + 1] in ("「", "“", "\"", "'"):
                    return ""  # 后面紧跟着原话引用，编号直接去掉
                return "「" + clip(reasons[key], 20) + "」"
            owner = owners.get(key)
            if owner is None:
                return m.group(0)
            if owner == "user":
                return "你刚才说的"
            if owner == speaker:
                return "我前面说的"
            return self.names.get(owner, "别人") + "的观点"

        text = _ID_REF.sub(sub, text)
        return re.sub(r"的观点 ?的", "的", text)

    def _plain(self, v):
        """决策卡里给用户看的文字不带内部编号（claim_ids、reason_id 这些字段保持原样）。"""
        if isinstance(v, dict):
            return {k: (x if k in ("claim_ids", "reason_id") else self._plain(x)) for k, x in v.items()}
        if isinstance(v, list):
            return [self._plain(x) for x in v]
        return self._humanize(v, "") if isinstance(v, str) else v

    # ----- 决策卡 -----
    def _stance_summary(self) -> list[dict]:
        rows = self.svc.store.stances(self.sid)
        out = []
        for p in self.team:
            mine = [r for r in rows if r["persona_id"] == p["id"]]
            if not mine:
                continue
            changes = []
            for a, b in zip(mine, mine[1:]):
                if a["position"] != b["position"]:
                    changes.append({"round": b["round"], "from": a["position"], "to": b["position"],
        "persuaded_by": b["persuaded_by"], "justified": b["justified"]})
            out.append({"persona_id": p["id"], "name": p["name"], "initial": mine[0]["position"],
                        "final": mine[-1]["position"], "note": mine[-1]["note"], "changes": changes,
                        "blocked_changes": sum(1 for r in mine if r["reverted"])})
        return out

    async def _card(self, event_type: str) -> None:
        svc = self.svc
        summary = self._stance_summary()
        lines = [f"- {x['name']}：{x['final']}（{prompts.POSITION_CN[x['final']]}）。{x['note']}" for x in summary]
        system, user = prompts.card(self.s, self.pool, self.names, lines, self.profile_note)
        out, _ = await svc.llm.json(
            "card", system, user, provider=svc.llm.judge, session_id=self.sid, temperature=0.3, max_tokens=1800,
            mock={"reasons": self.s["reasons"], "pool": self.pool, "leaning": self.s.get("leaning")},
        )
        card = self._plain(self._clean_card(out))
        pc = self.persona_claims()
        card["stances"] = summary
        card["stats"] = {
            "claims": len(pc),
            "challenges": sum(1 for c in pc if c["direction"] == "challenge"),
            "echoes": sum(len(c.get("echoed_by") or []) for c in pc),
            "rewrites": len(svc.store.events(self.sid, types=("quality.rejected",))),
            "rounds": self.round,
            "experiences_used": sum(len(v) for v in self.experiences.values()),
        }
        self.emit(event_type, {"card": card})

    def _clean_card(self, out: dict) -> dict:
        """核对决策卡：论点编号必须真实存在，每条理由都要有结论，建议必须带条件。"""
        ids = {c["id"] for c in self.pool}

        def refs(v) -> list[str]:
            return [x for x in v if isinstance(x, str) and x in ids] if isinstance(v, list) else []

        checks = dict()
        for rc in out.get("reason_checks") or []:
            if isinstance(rc, dict) and rc.get("reason_id"):
                checks[rc["reason_id"]] = rc
        reason_checks = []
        for r in self.s["reasons"]:
            rc = checks.get(r["id"]) or dict()
            reason_checks.append({
                "reason_id": r["id"], "reason": r["text"],
                "verdict": rc.get("verdict") if rc.get("verdict") in VERDICTS else "shaky",
                "summary": clip(str(rc.get("summary") or "这条理由在辩论里没有被充分检验。"), 300),
                "claim_ids": refs(rc.get("claim_ids")),
            })
        blind = [
            {"text": clip(str(b.get("text")), 200), "claim_ids": refs(b.get("claim_ids"))}
            for b in out.get("blind_spots") or [] if isinstance(b, dict) and b.get("text")
        ][:4]
        ct = out.get("core_tradeoff") if isinstance(out.get("core_tradeoff"), dict) else dict()
        recs = [
            {"condition": clip(str(x.get("condition")), 200), "suggestion": clip(str(x.get("suggestion")), 200)}
            for x in out.get("recommendations") or []
            if isinstance(x, dict) and x.get("condition") and x.get("suggestion")
        ][:3]
        if len(recs) < 2:
            raise LLMError("card: 决策卡没有给出至少两条带条件的建议")
        return {
            "reason_checks": reason_checks,
            "blind_spots": blind,
            "core_tradeoff": {"a": clip(str(ct.get("a") or ""), 30), "b": clip(str(ct.get("b") or ""), 30),
                "summary": clip(str(ct.get("summary") or ""), 200)},
            "recommendations": recs,
            "next_steps": [clip(str(x), 120) for x in out.get("next_steps") or [] if isinstance(x, str)][:3],
            "values": [clip(str(x), 12) for x in out.get("values") or [] if isinstance(x, str)][:4],
        }


async def regenerate_card(svc: Services, sid: str) -> dict:
    """决策卡生成失败时重新生成（辩论记录还在）。"""
    s = svc.store.get_session(sid)
    if svc.running(sid) or not svc.store.claims(sid):
        raise ValueError("no debate record to build a card from")
    d = Debate(svc, s)
    await d._card("card.updated" if s.get("card") else "card.ready")
    svc.emit(sid, "status", {"status": "completed"})
    svc.emit(sid, "done", {"status": "completed"})
    s = svc.store.get_session(sid)
    evolution.on_completed(svc, s, s["card"])
    return s["card"]
