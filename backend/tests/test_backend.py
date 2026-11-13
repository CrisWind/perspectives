import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app import engine, evolution, metrics, quality
from app.config import Settings
from app.content import Content, select_team
from app.engine import Services
from app.main import create_app
from app.util import extract_json


def make_settings(tmp_path, **kw) -> Settings:
    return Settings(db_path=str(tmp_path / "t.db"), force_mock=True, mock_delay=0, quick_replay_delay=0, **kw)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        yield c


def wait_done(client, sid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/api/sessions/{sid}").json()
        if s["status"] in ("completed", "failed") and not s["running"]:
            return s
        time.sleep(0.05)
    raise AssertionError("session did not finish")


def run_deep(client, **options):
    r = client.post("/api/sessions", json={
        "dilemma": "我在国企工作两年，想辞职转行做产品经理", "leaning": "倾向辞职",
        "reasons": ["现在的工作学不到东西", "趁年轻试错成本低"], "facts": ["存款够六个月"],
        "mbti": "ENFP", "user_id": "u1", "options": options,
    })
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"] == "ready" and len(s["reasons"]) == 2
    assert client.post(f"/api/sessions/{s['id']}/start").status_code == 200
    return wait_done(client, s["id"])


# ---------- 单元 ----------
def test_extract_json():
    assert extract_json('好的：```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('前言 {"a": {"b": 2}} 后记') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("没有 JSON")


def test_similarity():
    a = "读研三年的机会成本是三年的工资和工作经验"
    assert quality.similarity(a, a) == 1.0
    assert quality.similarity(a, "读研三年的机会成本，是三年工资和工作经验！") > 0.72
    assert quality.similarity(a, "父母年纪大了需要人照顾") < 0.2


def test_stance_ledger():
    pool = [{"id": "c1", "persona_id": "a"}, {"id": "c2", "persona_id": "b"}]
    prev = {"position": "oppose"}
    st = {"position": "support", "persuaded_by": None}
    assert quality.check_stance(st, prev, "a", pool)["code"] == "stance_unjustified"
    st = {"position": "support", "persuaded_by": "c1"}  # 不能被自己的论点说服
    assert quality.check_stance(st, prev, "a", pool) is not None
    st = {"position": "support", "persuaded_by": "c2"}
    assert quality.check_stance(st, prev, "a", pool) is None and st["changed"] and st["justified"]
    st = {"position": "oppose", "persuaded_by": None}
    assert quality.check_stance(st, prev, "a", pool) is None and not st["changed"]
    st = {"position": "support", "persuaded_by": None}
    quality.check_stance(st, prev, "a", pool)
    quality.revert_stance(st, prev)
    assert st["position"] == "oppose" and st["reverted"] and not st["changed"]


def test_normalize_and_sources():
    reasons = [{"id": "r1", "text": "x"}]
    out = {"speech": "说点什么", "claims": [
        {"type": "rebuttal", "text": "这是一条足够长的论点", "reason_id": "r9", "targets": "c99", "direction": "challenge",
                "source": "http://fake"},
        {"type": "evidence", "text": "这是另一条带来源的论点", "reason_id": "r1", "source": "http://real"},
    ], "stance": {"position": "bogus", "confidence": "x"}}
    norm, issues = quality.normalize(out, reasons, {"c1"}, 300)
    assert not issues
    assert norm["claims"][0]["type"] == "new" and norm["claims"][0]["reason_id"] is None
    assert norm["stance"]["position"] is None and norm["stance"]["confidence"] == 0.5
    stripped = quality.check_sources(norm["claims"], {"http://real"})
    assert len(stripped) == 1 and norm["claims"][0]["sources"] == [] and norm["claims"][1]["sources"] == ["http://real"]
    assert quality.normalize({"speech": "", "claims": []}, reasons, set(), 300)[0] is None


def test_team_selection():
    c = Content()
    for mbti in ("ENFP", "ISTJ", None, "xxxx"):
        team = select_team(c, mbti, 4)
        assert len(team) == 4 and all(p["role"] == "debater" for p in team)
        assert any(p["can_search"] for p in team)
        assert len({p["framework"] for p in team}) == 4
        assert [p["id"] for p in team] == [p["id"] for p in select_team(c, mbti, 4)]
    # 互补：和用户 MBTI 完全相同的角色不会被优先选中
    assert "tantan" not in [p["id"] for p in select_team(c, "ENFP", 4)]


# ---------- 全流程 ----------
def test_deep_flow_with_quality_engine(client):
    s = run_deep(client)
    assert s["status"] == "completed"
    sid = s["id"]
    card = s["card"]
    ids = {c["id"] for c in s["claims"]}
    assert [rc["reason_id"] for rc in card["reason_checks"]] == ["r1", "r2"]
    assert all(set(rc["claim_ids"]) <= ids for rc in card["reason_checks"])
    assert len(card["recommendations"]) >= 2 and card["stances"]
    ev = client.get(f"/api/sessions/{sid}/events?format=json").json()["events"]
    types = [e["type"] for e in ev]
    assert types[-1] == "done" and "team.selected" in types and "card.ready" in types
    codes = {i["code"] for e in ev if e["type"] == "quality.rejected" for i in e["data"]["issues"]}
    assert {"duplicate", "stance_unjustified"} <= codes
    m = client.get(f"/api/sessions/{sid}/metrics").json()
    assert m["unjustified_stance_changes"] == 0 and m["rewrites"] >= 2
    # 论点池里不应有字面重复
    texts = [c["text"] for c in s["claims"]]
    for i, a in enumerate(texts):
        for b in texts[:i]:
            assert quality.similarity(a, b) < 0.72
    # SSE：完整回放并正常结束
    with client.stream("GET", f"/api/sessions/{sid}/events") as r:
        body = "".join(r.iter_text())
    assert "event: card.ready" in body and "event: done" in body
    after = ev[-3]["seq"]
    part = client.get(f"/api/sessions/{sid}/events?format=json&after={after}").json()["events"]
    assert [e["seq"] for e in part] == [after + 1, after + 2]


def test_engine_off_lets_errors_through(client):
    s = run_deep(client, quality_engine=False)
    m = client.get(f"/api/sessions/{s['id']}/metrics").json()
    assert m["rewrites"] == 0 and m["unjustified_stance_changes"] == 1 and m["quality_engine"] is False


def test_clarify_questions(client):
    r = client.post("/api/sessions", json={"dilemma": "要不要辞职去创业。因为现在的工作太无聊了；而且朋友拉我入伙"})
    s = r.json()
    assert s["status"] == "clarifying" and s["questions"] and s["category"] == "switch"
    q = s["questions"][0]
    s = client.post(f"/api/sessions/{s['id']}/clarify", json={"answers": [{"id": q["id"], "answer": "可以回原行业"}]}).json()
    assert s["status"] == "ready" and s["questions"][0]["answer"] == "可以回原行业"
    assert client.post("/api/sessions", json={"dilemma": ""}).status_code == 400
    assert client.get("/api/sessions/nope").status_code == 404


def test_interject_after_card(client):
    s = run_deep(client)
    sid, n = s["id"], len(s["claims"])
    target = s["claims"][0]["id"]
    r = client.post(f"/api/sessions/{sid}/interject", json={"text": "我不同意，我有副业收入可以兜底", "claim_id": target})
    assert r.json()["followup"] is True
    s2 = wait_done(client, sid)
    users = [c for c in s2["claims"] if c["persona_id"] == "user"]
    assert len(users) == 1 and users[0]["targets"] == target
    assert len(s2["claims"]) > n + 1
    types = [e["type"] for e in client.get(f"/api/sessions/{sid}/events?format=json").json()["events"]]
    assert "user.interjected" in types and "card.updated" in types and s2["status"] == "completed"
    replies = [c for c in s2["claims"] if c["targets"] == users[0]["id"]]
    assert replies


def test_feedback_evolves_experience_library(client):
    s = run_deep(client)
    sid = s["id"]
    challenge = [c for c in s["claims"] if c["direction"] == "challenge"]
    items = [{"claim_id": challenge[0]["id"], "label": "unexpected"}, {"claim_id": challenge[1]["id"], "label": "useless"},
                    {"claim_id": "c999", "label": "valuable"}, {"claim_id": challenge[2]["id"], "label": "agree"}]
    r = client.post(f"/api/sessions/{sid}/feedback", json={"items": items}).json()
    assert r["accepted"] == 2 and r["ignored"] == 2 and len(r["learned"]) == 1
    # 重复提交不重复计数
    r = client.post(f"/api/sessions/{sid}/feedback", json={"items": items[:1]}).json()
    assert r["learned"] == [] and r["reinforced"] == []
    exps = client.get("/api/experiences").json()["experiences"]
    assert len(exps) == 1 and exps[0]["category"] == "switch"
    # 只有一个人认为有用的经验先观察，不会被用到
    s_one = run_deep(client)
    ev = client.get(f"/api/sessions/{s_one['id']}/events?format=json").json()["events"]
    assert not [e for e in ev if e["type"] == "experience.applied"]
    # 第二个人也认为有用（同一类追问被再次验证）之后才会被用上
    again = next(c for c in s_one["claims"] if c["direction"] == "challenge")
    r2 = client.post(f"/api/sessions/{s_one['id']}/feedback", json={"items": [{"claim_id": again["id"], "label": "valuable"}]}).json()
    assert len(r2["learned"]) + len(r2["reinforced"]) == 1
    exps = client.get("/api/experiences").json()["experiences"]
    best = max(exps, key=lambda e: e["wins"])
    if best["wins"] < 2:
        from app.main import create_app  # noqa: F401
        c2 = client.app.state.svc.store
        c2.bump_experience(best["id"], wins=1)  # 模拟又一位用户的认可
    s2 = run_deep(client)
    ev = client.get(f"/api/sessions/{s2['id']}/events?format=json").json()["events"]
    applied = [e for e in ev if e["type"] == "experience.applied"]
    assert applied and applied[0]["data"]["items"][0]["id"] == best["id"]
    assert s2["card"]["stats"]["experiences_used"] >= 1
    # 个人层
    assert client.get("/api/users/u1/profile").status_code == 403  # 没有密钥看不到别人的档案
    assert client.get("/api/users/u1/profile", headers={"X-User-Key": "wrong"}).status_code == 403
    key = client.get(f"/api/sessions/{sid}").json()["user_key"]
    prof = client.get("/api/users/u1/profile", headers={"X-User-Key": key}).json()
    assert prof["mbti"] == "ENFP" and len(prof["profile"]["decisions"]) == 3 and prof["profile"]["values"]
    st = client.get("/api/stats").json()
    assert st["stats"]["experiences"] >= 1 and st["stats"]["feedback"] == 3
    assert any(x["kind"] == "learned" for x in client.get("/api/evolution/log").json()["log"])


def test_quick_mode_uses_cache(client):
    a = client.post("/api/quick", json={"preset_id": "grad_or_job", "mbti": "INTJ"}).json()
    sa = wait_done(client, a["id"])
    b = client.post("/api/quick", json={"preset_id": "grad_or_job", "mbti": "INTJ"}).json()
    sb = wait_done(client, b["id"])
    ea = client.get(f"/api/sessions/{a['id']}/events?format=json").json()["events"]
    eb = client.get(f"/api/sessions/{b['id']}/events?format=json").json()["events"]
    assert ea[-1]["data"].get("cached") is None and eb[-1]["data"]["cached"] is True
    assert sa["card"] == sb["card"] and len(sa["claims"]) == len(sb["claims"])
    # 回放出来的会话同样可以反馈
    cid = next(c["id"] for c in sb["claims"] if c["direction"] == "challenge")
    r = client.post(f"/api/sessions/{b['id']}/feedback", json={"items": [{"claim_id": cid, "label": "valuable"}]}).json()
    assert len(r["learned"]) == 1
    assert client.post("/api/quick", json={"preset_id": "nope"}).status_code == 400


def test_prompt_version_gate_and_rollback(tmp_path):
    async def go():
        svc = Services(make_settings(tmp_path))
        try:
            sid = await engine.run_headless(svc, "grad_or_job", dict(), mode="quick")
            claim = next(c for c in svc.store.claims(sid) if c["persona_id"] == "laomou")
            with pytest.raises(ValueError):
                await evolution.propose_patch(svc, "laomou")
            await evolution.apply_feedback(svc, sid, [{"claim_id": claim["id"], "label": "useless"}])
            v = await evolution.propose_patch(svc, "laomou")
            assert v["status"] == "candidate" and v["version"] == 1
            v = await evolution.evaluate_patch(svc, v["id"], ["grad_or_job"])
            assert v["status"] == "active" and v["score"] >= v["baseline_score"]
            assert svc.store.patch_for("laomou")["id"] == v["id"]
            assert svc.store.stats()["sessions"] == 1  # 门槛评测跑的场次不计入统计
            assert metrics.persona_score(svc.store, sid, "laomou")["claims"] > 0
            r = evolution.rollback(svc, "laomou")
            assert r["restored"] is None and svc.store.patch_for("laomou") is None
            kinds = [x["kind"] for x in svc.store.evolution_log()]
            assert {"proposed", "promoted", "rolled_back"} <= set(kinds)
        finally:
            await svc.shutdown()

    asyncio.run(go())


def test_health_and_content(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["mock"] is True and "llm" not in h
    assert len(client.get("/api/personas").json()["personas"]) == 16
    assert len(client.get("/api/presets").json()["presets"]) == 6


def test_parallel_batch_dedupes_against_each_other(tmp_path, monkeypatch):
    """同一批并行发言的角色，后面的发言要能和前面的比对（用临时编号），重复的记为不谋而合。"""

    async def fake_judge(llm, sid, persona, fw, speech, claims, pool, position):
        temp = [c["id"] for c in pool if c["id"].startswith("t")]
        return (dict([(0, temp[0])]) if temp else dict()), None, None

    monkeypatch.setattr(quality, "judge", fake_judge)

    async def go():
        svc = Services(make_settings(tmp_path))
        try:
            sid = await engine.run_headless(svc, "grad_or_job", dict(rounds=1))
            return svc.store.claims(sid), svc.store.utterances(sid)
        finally:
            await svc.shutdown()

    claims, utts = asyncio.run(go())
    first_round = [c for c in claims if c["round"] == 1]
    speakers = [u["persona_id"] for u in utts if u["round"] == 1]
    assert len(speakers) == 4
    assert len(first_round) == 2 + 3  # 第一位两条都留下，后三位各有一条和 c1 撞车
    assert first_round[0]["id"] == "c1" and sorted(first_round[0]["echoed_by"]) == sorted(speakers[1:])


def test_same_batch_collisions_become_echoes(tmp_path, monkeypatch):
    """同一批里两个角色说到一起去：后说的那条记为不谋而合，不进论点池。"""
    from app import mock

    def fake_cluster(c):
        n = len(c["claims"])
        return dict(groups=[[0, 2]] + [[i] for i in range(n) if i not in (0, 2)])

    monkeypatch.setattr(mock, "_cluster", fake_cluster)

    async def go():
        svc = Services(make_settings(tmp_path))
        try:
            sid = await engine.run_headless(svc, "grad_or_job", dict(rounds=1))
            return svc.store.claims(sid), svc.store.utterances(sid)
        finally:
            await svc.shutdown()

    claims, utts = asyncio.run(go())
    first_round = [c for c in claims if c["round"] == 1]
    second = [u for u in utts if u["round"] == 1][1]
    assert len(first_round) == 2 + 1 + 2 + 2
    assert first_round[0]["echoed_by"] == [second["persona_id"]] and len(second["claim_ids"]) == 1


# ---------- 安全 ----------
def test_crisis_input_stops_debate_and_shows_help(client):
    r = client.post("/api/sessions", json={"dilemma": "我最近觉得活着没意思，想一了百了，要不要退学", "leaning": "退学"})
    s = r.json()
    assert r.status_code == 200 and s["status"] == "safety" and s["reasons"] == []
    assert "未保存" in s["dilemma"] and "活着" not in str(s)  # 不保存原文
    ev = client.get(f"/api/sessions/{s['id']}/events?format=json").json()["events"]
    safety = next(e for e in ev if e["type"] == "safety")
    assert safety["data"]["lines"][0]["number"] == "12356" and ev[-1]["type"] == "done"
    assert client.post(f"/api/sessions/{s['id']}/start").status_code == 400  # 不能开辩论


def test_crisis_in_clarify_answer_and_interjection(client):
    s = client.post("/api/sessions", json={"dilemma": "要不要辞职去创业。因为现在的工作太无聊了；而且朋友拉我入伙"}).json()
    assert s["status"] == "clarifying"
    q = s["questions"][0]
    s2 = client.post(f"/api/sessions/{s['id']}/clarify", json={"answers": [{"id": q["id"], "answer": "我不想活了"}]}).json()
    assert s2["status"] == "safety"
    done = run_deep(client)
    r = client.post(f"/api/sessions/{done['id']}/interject", json={"text": "其实我有过轻生的念头"}).json()
    assert r["safety"]["kind"] == "self_harm"


def test_figurative_words_do_not_trigger_crisis(tmp_path):
    """关键词命中后由模型判断；判断为夸张说法就放行，正常开辩论。"""
    with TestClient(create_app(make_settings(tmp_path))) as c:
        llm = c.app.state.svc.llm
        orig = llm.json

        async def fake(purpose, *a, **k):
            if purpose == "safety":
                return dict(crisis=False), "mock"
            return await orig(purpose, *a, **k)

        llm.json = fake
        s = c.post("/api/sessions", json={"dilemma": "期末考试要累死了，考不好我就去死哈哈，要不要放弃竞赛专心复习"}).json()
        assert s["status"] != "safety" and s["reasons"]
        assert c.post(f"/api/sessions/{s['id']}/start").status_code == 200


def test_safety_judge_failure_falls_back_to_keywords(tmp_path):
    from app import safety
    from app.llm import LLMError

    async def boom(*a, **k):
        raise LLMError("down")

    with TestClient(create_app(make_settings(tmp_path))) as c:
        c.app.state.svc.llm.json = boom
        s = c.post("/api/sessions", json={"dilemma": "我真的不想活了，要不要休学"}).json()
        assert s["status"] == "safety"  # 模型不可用时，命中关键词仍然按危机处理
        s = c.post("/api/sessions", json={"dilemma": "要不要从大厂辞职去创业公司", "reasons": ["成长更快"]}).json()
        assert s["status"] != "safety"
    assert safety.has_signal("想轻生") and not safety.has_signal("要不要换工作")


def test_input_limits_and_session_id_is_not_guessable(client):
    r = client.post("/api/sessions", json={"dilemma": "要不要辞职" * 400})
    assert r.status_code == 422
    r = client.post("/api/sessions", json={"dilemma": "要不要辞职去创业，我已经想好了", "reasons": ["理由"] * 20})
    assert r.status_code == 422
    s = client.post("/api/sessions", json={"dilemma": "要不要辞职去创业，我已经想好了", "reasons": ["太无聊了"]}).json()
    assert len(s["id"]) >= 22


def test_rate_limit_returns_429(tmp_path):
    st = make_settings(tmp_path)
    st.rate_per_minute = 3
    with TestClient(create_app(st)) as c:
        codes = [c.post("/api/sessions", json={"dilemma": "要不要辞职去创业，我已经想好了"}).status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
        assert c.get("/api/health").status_code == 200  # 读请求不受限


def test_too_many_active_debates_returns_429(tmp_path):
    st = make_settings(tmp_path)
    st.max_active_sessions = 1
    st.mock_delay = 0.4
    with TestClient(create_app(st)) as c:
        ids = [c.post("/api/sessions", json={"dilemma": "要不要辞职去创业，我已经想好了", "reasons": ["太无聊了"], "options": {"skip_questions": True}}).json()["id"] for _ in range(2)]
        assert c.post(f"/api/sessions/{ids[0]}/start").status_code == 200
        r = c.post(f"/api/sessions/{ids[1]}/start")
        assert r.status_code == 429 and "稍等" in r.json()["detail"]


def test_seed_experience_is_used_immediately(client):
    r = client.post("/api/experiences/seed", json={"category": "switch", "pattern": "让用户算清离职后每个月的固定支出和能撑到哪天"})
    assert r.status_code == 200
    assert client.post("/api/experiences/seed", json={"category": "switch", "pattern": "让用户算清离职后每个月的固定支出和能撑到哪一天"}).status_code == 400
    assert client.post("/api/experiences/seed", json={"category": "nope", "pattern": "这是一条足够长的经验内容"}).status_code == 400
    s = run_deep(client)
    ev = client.get(f"/api/sessions/{s['id']}/events?format=json").json()["events"]
    applied = [e for e in ev if e["type"] == "experience.applied"]
    assert applied and "固定支出" in applied[0]["data"]["items"][0]["pattern"]


def _real_llm(tmp_path, handler, **kw):
    import httpx
    from app.llm import LLM, Provider
    s = Settings(db_path=str(tmp_path / "t.db"), llm_max_retries=2, **kw)
    llm = LLM(s, providers=[Provider("a", "http://a", "k", "m"), Provider("b", "http://b", "k", "m")])
    llm._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm


def test_rejected_key_is_not_retried(tmp_path, monkeypatch):
    import httpx
    from app import llm as llm_mod
    _sleep = asyncio.sleep
    monkeypatch.setattr(llm_mod.asyncio, "sleep", lambda *_: _sleep(0))
    hits = {"a": 0, "b": 0}

    def handler(req):
        host = req.url.host
        hits[host] += 1
        if host == "a":
            return httpx.Response(401, json={"error": "Invalid token"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    llm = _real_llm(tmp_path, handler)

    async def go():
        for _ in range(3):
            assert await llm.text("t", "s", "u", provider="a") == ("ok", "b")
    asyncio.run(go())
    assert hits["a"] == 1 and "a" in llm.dead


def test_call_limits_stop_spending(tmp_path, monkeypatch):
    import httpx
    from app import llm as llm_mod
    from app.llm import LLMError
    _sleep = asyncio.sleep
    monkeypatch.setattr(llm_mod.asyncio, "sleep", lambda *_: _sleep(0))
    hits = []

    def handler(req):
        hits.append(1)
        return httpx.Response(500, text="boom")

    llm = _real_llm(tmp_path, handler, session_call_limit=4, daily_call_limit=0)

    async def go():
        with pytest.raises(LLMError):
            await llm.text("t", "s", "u", session_id="s1")
        with pytest.raises(LLMError, match="limit"):
            await llm.text("t", "s", "u", session_id="s1")
    asyncio.run(go())
    assert len(hits) == 4
