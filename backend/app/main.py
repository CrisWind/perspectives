"""HTTP 接口。启动：uv run uvicorn app.main:create_app --factory --port 8000"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import engine, evolution, metrics
from .config import Settings
from .content import CATEGORIES
from .engine import Services

log = logging.getLogger("perspectives.api")


class SessionIn(BaseModel):
    dilemma: str = ""
    leaning: str = ""
    reasons: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    mbti: str | None = None
    user_id: str | None = None
    mode: str = "deep"
    preset_id: str | None = None
    options: dict = Field(default_factory=dict)


class QuickIn(BaseModel):
    preset_id: str
    mbti: str | None = None
    user_id: str | None = None


class Answer(BaseModel):
    id: str
    answer: str


class ClarifyIn(BaseModel):
    answers: list[Answer] = Field(default_factory=list)
    reasons: list[str] | None = None


class InterjectIn(BaseModel):
    text: str
    reason_id: str | None = None
    claim_id: str | None = None


class FeedbackItem(BaseModel):
    claim_id: str
    label: str


class FeedbackIn(BaseModel):
    items: list[FeedbackItem]
    user_id: str | None = None


class EvaluateIn(BaseModel):
    preset_ids: list[str] | None = None


# 普通用户不能通过接口设置的选项（评测脚本直接调用引擎，不走接口）
PUBLIC_OPTIONS = ("quality_engine", "rounds", "team", "use_experience", "use_cache", "skip_questions")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = Services(settings)
        n = svc.store.interrupt_running()
        if n:
            log.warning("marked %d interrupted sessions as failed", n)
        app.state.svc = svc
        app.state.jobs = set()
        log.info("models: %s", svc.llm.info())
        yield
        await svc.shutdown()

    app = FastAPI(title="Perspectives API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"],
        expose_headers=["*"],
    )

    def svc_of(request: Request) -> Services:
        return request.app.state.svc

    def admin(x_admin_token: str | None = Header(default=None)) -> None:
        if settings.admin_token and x_admin_token != settings.admin_token:
            raise HTTPException(403, "admin token required")

    def session_or_404(svc: Services, sid: str) -> dict:
        s = svc.store.get_session(sid)
        if not s:
            raise HTTPException(404, "session not found")
        return s

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def view(svc: Services, s: dict) -> dict:
        sid = s["id"]
        return {
            **s,
            "running": svc.running(sid),
            "last_seq": svc.store.last_seq(sid),
            "claims": svc.store.claims(sid),
            "utterances": svc.store.utterances(sid),
            "stances": svc.store.stances(sid),
            "feedback": svc.store.feedback(sid),
        }

    # ---------- 基础信息 ----------
    @app.get("/api/health")
    def health(svc: Services = Depends(svc_of)):
        return {"ok": True, "llm": svc.llm.info(), "search": svc.search.enabled,
                "content_version": svc.content.version}

    @app.get("/api/personas")
    def personas(svc: Services = Depends(svc_of)):
        return {"personas": [svc.content.public_persona(p) for p in svc.content.personas.values()],
                "frameworks": list(svc.content.frameworks.values())}

    @app.get("/api/presets")
    def presets(svc: Services = Depends(svc_of)):
        return {"presets": list(svc.content.presets.values()), "categories": CATEGORIES}

    # ---------- 会话 ----------
    @app.post("/api/sessions")
    async def create(body: SessionIn, svc: Services = Depends(svc_of)):
        req = body.model_dump()
        if req["mode"] not in ("quick", "deep"):
            raise ValueError("mode must be quick or deep")
        req["options"] = {k: v for k, v in req["options"].items() if k in PUBLIC_OPTIONS}
        s = await engine.create_session(svc, req)
        return view(svc, s)

    @app.post("/api/quick")
    async def quick(body: QuickIn, svc: Services = Depends(svc_of)):
        """快速模式一步到位：选预设题和 MBTI，直接开始。"""
        s = await engine.create_session(svc, {"mode": "quick", **body.model_dump()})
        engine.start(svc, s["id"])
        return view(svc, svc.store.get_session(s["id"]))

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str, svc: Services = Depends(svc_of)):
        return view(svc, session_or_404(svc, sid))

    @app.post("/api/sessions/{sid}/clarify")
    def clarify(sid: str, body: ClarifyIn, svc: Services = Depends(svc_of)):
        session_or_404(svc, sid)
        s = engine.answer_clarify(svc, sid, [a.model_dump() for a in body.answers], body.reasons)
        return view(svc, s)

    @app.post("/api/sessions/{sid}/start")
    async def start(sid: str, svc: Services = Depends(svc_of)):
        session_or_404(svc, sid)
        engine.start(svc, sid)
        return {"ok": True, "status": "running"}

    @app.get("/api/sessions/{sid}/events")
    async def events(
        sid: str,
        request: Request,
        after: int = Query(0, ge=0),
        format: str = Query("sse"),
        last_event_id: str | None = Header(default=None),
        svc: Services = Depends(svc_of),
    ):
        """事件流（SSE）。断线重连时带上 after=已收到的最大 seq（或标准的 Last-Event-ID）。

        format=json 时一次性返回 after 之后的事件，给不方便用 SSE 的客户端轮询。
        """
        session_or_404(svc, sid)
        if last_event_id and last_event_id.isdigit():
            after = max(after, int(last_event_id))
        if format == "json":
            s = svc.store.get_session(sid)
            return {"events": svc.store.events(sid, after), "status": s["status"], "running": svc.running(sid)}

        async def stream():
            seq = after
            idle = 0.0
            yield "retry: 2000\n\n"
            while True:
                batch = svc.store.events(sid, seq)
                for ev in batch:
                    seq = ev["seq"]
                    payload = json.dumps({"seq": seq, "ts": ev["ts"], **ev["data"]}, ensure_ascii=False)
                    yield f"id: {seq}\nevent: {ev['type']}\ndata: {payload}\n\n"
                if batch:
                    idle = 0.0
                    continue
                s = svc.store.get_session(sid)
                finished = s["status"] in ("completed", "failed") and not svc.running(sid)
                if finished or idle > 900 or await request.is_disconnected():
                    break
                await svc.wait(sid, 10)
                if svc.store.last_seq(sid) == seq:
                    idle += 10
                    yield ": ping\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
        })

    @app.post("/api/sessions/{sid}/interject")
    async def interject(sid: str, body: InterjectIn, svc: Services = Depends(svc_of)):
        session_or_404(svc, sid)
        return engine.interject(svc, sid, body.text, body.reason_id, body.claim_id)

    @app.post("/api/sessions/{sid}/feedback")
    async def feedback(sid: str, body: FeedbackIn, svc: Services = Depends(svc_of)):
        session_or_404(svc, sid)
        return await evolution.apply_feedback(svc, sid, [i.model_dump() for i in body.items], body.user_id)

    @app.post("/api/sessions/{sid}/card")
    async def card(sid: str, svc: Services = Depends(svc_of)):
        session_or_404(svc, sid)
        return {"card": await engine.regenerate_card(svc, sid)}

    @app.get("/api/sessions/{sid}/metrics")
    def session_metrics(sid: str, svc: Services = Depends(svc_of)):
        session_or_404(svc, sid)
        return metrics.session_metrics(svc.store, sid)

    # ---------- 进化 ----------
    @app.get("/api/users/{user_id}/profile")
    def profile(user_id: str, svc: Services = Depends(svc_of)):
        row = svc.store.get_profile(user_id)
        return {"user_id": user_id, "mbti": (row or dict()).get("mbti"),
                "profile": evolution.load_profile(svc.store, user_id)}

    @app.get("/api/experiences")
    def experiences(category: str | None = None, limit: int = Query(50, le=200), svc: Services = Depends(svc_of)):
        return {"experiences": svc.store.experiences(category, limit)}

    @app.get("/api/evolution/log")
    def evolution_log(limit: int = Query(50, le=200), svc: Services = Depends(svc_of)):
        return {"log": svc.store.evolution_log(limit)}

    @app.get("/api/evolution/versions")
    def versions(persona_id: str | None = None, svc: Services = Depends(svc_of)):
        return {"versions": svc.store.prompt_versions(persona_id)}

    @app.post("/api/evolution/personas/{persona_id}/propose", dependencies=[Depends(admin)])
    async def propose(persona_id: str, svc: Services = Depends(svc_of)):
        if persona_id not in svc.content.personas:
            raise HTTPException(404, "persona not found")
        return await evolution.propose_patch(svc, persona_id)

    @app.post("/api/evolution/versions/{version_id}/evaluate", dependencies=[Depends(admin)])
    async def evaluate(version_id: int, body: EvaluateIn, request: Request, svc: Services = Depends(svc_of)):
        """门槛评测要跑好几场辩论，放后台执行；结果看 /api/evolution/versions 和 /api/evolution/log。"""
        v = svc.store.get_prompt_version(version_id)
        if not v or v["status"] != "candidate":
            raise ValueError("只能评测候选版本")
        svc.store.set_prompt_status(version_id, "candidate")
        job = asyncio.create_task(evolution.evaluate_patch(svc, version_id, body.preset_ids))
        jobs = request.app.state.jobs
        jobs.add(job)

        def _done(t: asyncio.Task) -> None:
            jobs.discard(t)
            if not t.cancelled() and t.exception():
                log.error("patch evaluation failed: %r", t.exception())

        job.add_done_callback(_done)
        return {"started": True, "version_id": version_id}

    @app.post("/api/evolution/personas/{persona_id}/rollback", dependencies=[Depends(admin)])
    def rollback(persona_id: str, svc: Services = Depends(svc_of)):
        if persona_id not in svc.content.personas:
            raise HTTPException(404, "persona not found")
        return evolution.rollback(svc, persona_id)

    # ---------- 大屏 ----------
    @app.get("/api/stats")
    def stats(svc: Services = Depends(svc_of)):
        return {"stats": svc.store.stats(), "llm": svc.store.llm_usage(),
                "recent_evolution": svc.store.evolution_log(10),
                "top_experiences": svc.store.experiences(None, 5)}

    return app
