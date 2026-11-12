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
    # 下一场同类辩论会用上这条经验
    s2 = run_deep(client)
    ev = client.get(f"/api/sessions/{s2['id']}/events?format=json").json()["events"]
    applied = [e for e in ev if e["type"] == "experience.applied"]
    assert applied and applied[0]["data"]["items"][0]["id"] == exps[0]["id"]
    assert s2["card"]["stats"]["experiences_used"] >= 1
    # 个人层
    prof = client.get("/api/users/u1/profile").json()
    assert prof["mbti"] == "ENFP" and len(prof["profile"]["decisions"]) == 2 and prof["profile"]["values"]
    st = client.get("/api/stats").json()
    assert st["stats"]["experiences"] == 1 and st["stats"]["feedback"] == 2
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
    assert h["ok"] and h["llm"]["mock"] is True
    assert len(client.get("/api/personas").json()["personas"]) == 8
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
