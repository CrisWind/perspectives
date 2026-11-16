"""赛道、主持 AI、联网检索、岗位对照卡、用户背景的测试。全部使用模拟模型，不消耗额度。"""
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from app import mock as mock_mod
from app import prompts
from app.config import CONTENT_DIR
from app.content import Content, pick_roles, role_block
from app.main import create_app
from app.search import Search, _day
from app.store import Store

from test_backend import make_settings, run_deep, wait_done


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        yield c


def events(c, sid):
    return c.get(f"/api/sessions/{sid}/events?format=json").json()["events"]


def decision(**kw):
    return dict({"action": "continue", "reason": "r", "gap": "", "question": "", "focus": None}, **kw)


def test_tracks_are_exposed_and_routed(client):
    p = client.get("/api/presets").json()
    assert set(p["tracks"]) == {"career", "life"} and "relation" in p["categories"]
    by = {x["id"]: x["track"] for x in p["presets"]}
    assert by["product_or_ops"] == "career" and by["roommate_conflict"] == "life" and by["change_major"] == "life"
    s = client.post("/api/sessions", json={"dilemma": "我和室友因为作息问题闷了两个月，要不要摊牌", "options": {"skip_questions": True}}).json()
    assert s["track"] == "life" and s["category"] == "relation"
    s = client.post("/api/sessions", json={"dilemma": "我拿到了后端开发和产品经理两个 offer，不知道选哪个", "options": {"skip_questions": True}}).json()
    assert s["track"] == "career"
    assert client.post("/api/sessions", json={"dilemma": "我拿到了两个 offer 不知道选哪个", "track": "nope"}).status_code == 400


def test_profile_is_stored_and_checked_for_safety(client):
    dilemma = "我拿到了后端开发和产品经理两个 offer，不知道选哪个"
    r = client.post("/api/sessions", json={
        "dilemma": dilemma, "options": {"skip_questions": True},
        "profile": {"major": "计算机", "grade": "大四", "energized": "和人讨论方案", "drained": "一个人调试到半夜", "bogus": "x"},
    })
    s = r.json()
    assert r.status_code == 200 and s["profile"]["major"] == "计算机" and "bogus" not in s["profile"]
    bad = client.post("/api/sessions", json={"dilemma": dilemma, "profile": {"drained": "我不想活了，总想自杀"}})
    assert bad.json()["status"] == "safety"


def test_moderator_decides_and_is_visible(client):
    s = run_deep(client)
    dec = [e["data"] for e in events(client, s["id"]) if e["type"] == "moderator.decision"]
    assert dec and all(d["action"] in ("continue", "search", "ask_user", "wrap_up") and d["reason"] for d in dec)
    assert s["status"] == "completed" and s["card"]["to_verify"]
    s2 = run_deep(client, moderator=False)
    assert not [e for e in events(client, s2["id"]) if e["type"] == "moderator.decision"]


def test_moderator_cannot_end_debate_while_challenges_are_short(tmp_path, monkeypatch):
    """防讨好底线：挑战不够数时，主持人说收尾也不行。"""
    monkeypatch.setattr(mock_mod, "_moderator", lambda c: decision(action="wrap_up", reason="够了"))
    with TestClient(create_app(make_settings(tmp_path, min_challenges=999))) as c:
        s = run_deep(c)
        dec = [e["data"] for e in events(c, s["id"]) if e["type"] == "moderator.decision"]
        assert dec and all(d["action"] == "continue" for d in dec)


def test_moderator_can_stop_early(tmp_path, monkeypatch):
    monkeypatch.setattr(mock_mod, "_moderator", lambda c: decision(action="wrap_up", reason="够了"))
    with TestClient(create_app(make_settings(tmp_path, min_challenges=0))) as c:
        s = run_deep(c, rounds=5)
        assert max(u["round"] for u in s["utterances"]) < 5 and s["card"]


def test_moderator_failure_does_not_block(tmp_path, monkeypatch):
    def boom(c):
        raise RuntimeError("x")

    monkeypatch.setattr(mock_mod, "_moderator", boom)
    with TestClient(create_app(make_settings(tmp_path))) as c:
        assert run_deep(c)["status"] in ("completed", "failed")


def test_search_dates_are_normalised():
    assert _day("2025-03-04T10:00:00Z") == "2025-03-04" and _day("昨天") == "" and _day(None) == ""


def test_supplementary_search_and_sources_on_card(tmp_path, monkeypatch):
    calls = []

    async def fake_search(self, query, k=4):
        calls.append(query)
        n = len(calls)
        return [{"title": f"资料{n}", "url": f"https://example.com/{n}", "snippet": "岗位介绍", "date": f"2025-03-0{n}"}]

    monkeypatch.setattr(Search, "search", fake_search)
    steps = iter([decision(action="search", reason="岗位差别需要核实", gap="产品岗和运营岗的区别")])
    monkeypatch.setattr(mock_mod, "_moderator", lambda c: next(steps, decision()))
    with TestClient(create_app(make_settings(tmp_path))) as c:
        c.app.state.svc.search.enabled = True
        s = run_deep(c)
        done = [e["data"] for e in events(c, s["id"]) if e["type"] == "search.done"]
        assert len(done) >= 2 and done[0]["supplement"] is False and done[1]["supplement"] is True
        assert all(r["date"].startswith("2025-") for d in done for r in d["results"])
        assert {x["url"] for x in s["card"]["sources"]} >= {"https://example.com/1", "https://example.com/2"}
        assert all("date" in x and "cited" in x for x in s["card"]["sources"])


def roles_dir(tmp_path, checked: bool):
    d = tmp_path / "content"
    shutil.copytree(CONTENT_DIR, d, dirs_exist_ok=True)
    by = "甲" if checked else ""
    cards = [
        {"id": "pm", "name": "产品经理", "aliases": ["产品岗"], "scope": "决定做什么", "daily": "写需求、对齐研发",
         "entry": "沟通能力", "path": "高级产品", "differs": {"ops": "产品管做什么，运营管怎么让人用"},
         "sources": [{"title": "某招聘启事", "url": "https://example.com/pm", "date": "2025-01-01"}], "checked_by": by},
        {"id": "ops", "name": "运营", "aliases": [], "scope": "让产品被用起来", "daily": "做活动", "entry": "执行力",
         "path": "运营负责人", "differs": {"pm": "运营管增长，产品管功能"}, "sources": [], "checked_by": by},
    ]
    (d / "roles.yaml").write_text(yaml.safe_dump(cards, allow_unicode=True), encoding="utf-8")
    return d


def test_unchecked_role_cards_are_not_used(tmp_path):
    assert Content(roles_dir(tmp_path, checked=False)).roles == {}
    assert Content().roles == {}  # 正式文件里的草稿没有核对人，不生效


def test_checked_role_cards_reach_the_debate_and_card(tmp_path):
    content = Content(roles_dir(tmp_path, checked=True))
    picked = pick_roles(content, "我在纠结产品岗要不要去")
    assert [r["id"] for r in picked] == ["pm", "ops"]  # 只提到一个，会补上容易混淆的那个
    assert "产品管做什么" in role_block(picked) and "2025-01-01" in role_block(picked)
    with TestClient(create_app(make_settings(tmp_path))) as c:
        c.app.state.svc.content = content
        assert {r["id"] for r in c.get("/api/roles").json()["roles"]} == {"pm", "ops"}
        sid = c.post("/api/sessions", json={"dilemma": "我拿到了产品岗和运营岗两个 offer，不知道选哪个", "leaning": "倾向产品岗",
                                            "reasons": ["产品岗更核心"], "options": {"skip_questions": True}}).json()["id"]
        c.post(f"/api/sessions/{sid}/start")
        s = wait_done(c, sid)
        assert any(e["type"] == "roles.loaded" for e in events(c, sid))
        assert [r["id"] for r in s["card"]["roles_used"]] == ["pm", "ops"]


def test_life_track_does_not_load_role_cards(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        c.app.state.svc.content = Content(roles_dir(tmp_path, checked=True))
        sid = c.post("/api/sessions", json={"dilemma": "我和室友因为作息问题闷了两个月，要不要摊牌，产品岗运营岗的事先不聊",
                                            "options": {"skip_questions": True}}).json()["id"]
        assert c.get(f"/api/sessions/{sid}").json()["track"] == "life"
        c.post(f"/api/sessions/{sid}/start")
        wait_done(c, sid)
        assert not [e for e in events(c, sid) if e["type"] == "roles.loaded"]


def test_prompts_differ_by_track():
    c = Content()
    p, fw = c.personas["laomou"], c.frameworks["premortem"]
    career = prompts.persona_system(p, fw, 260, None, True, "career")
    life = prompts.persona_system(p, fw, 260, None, True, "life")
    assert "职业选择的事实纪律" in career and "生活烦恼的边界" not in career
    assert "生活烦恼的边界" in life and "不是心理咨询师" in life and "职业选择的事实纪律" not in life


def test_old_database_is_upgraded(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, user_id TEXT, mode TEXT, status TEXT, category TEXT, dilemma TEXT, "
                "leaning TEXT, mbti TEXT, preset_id TEXT, reasons TEXT, questions TEXT, facts TEXT, team TEXT, card TEXT, "
                "options TEXT, created_at TEXT, updated_at TEXT)")
    con.commit()
    con.close()
    st = Store(path)
    s = st.create_session({"id": "s_old", "mode": "deep", "status": "ready", "category": "job", "track": "career",
                           "profile": {"major": "计算机"}, "dilemma": "测试"})
    assert s["track"] == "career" and s["profile"]["major"] == "计算机"
    st.close()


def test_ask_user_does_not_block_and_answer_goes_through_interject(tmp_path, monkeypatch):
    """主持人向用户提问后不停下等待；用户用插话接口回答，下一轮角色会回应。"""
    steps = iter([decision(action="ask_user", reason="缺只有你知道的事实", question="你有没有做过相关实习？")])
    monkeypatch.setattr(mock_mod, "_moderator", lambda c: next(steps, decision()))
    with TestClient(create_app(make_settings(tmp_path))) as c:
        s = run_deep(c)
        qs = [e["data"] for e in events(c, s["id"]) if e["type"] == "moderator.question"]
        assert qs and qs[0]["question"] == "你有没有做过相关实习？" and s["status"] == "completed"
        r = c.post(f"/api/sessions/{s['id']}/interject", json={"text": "做过三个月的运营实习"})
        assert r.json()["followup"] is True
        s2 = wait_done(c, s["id"])
        assert any(cl["persona_id"] == "user" for cl in s2["claims"])
