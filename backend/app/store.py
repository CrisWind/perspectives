"""SQLite 存储。辩论过程以事件流为准：写入事件时同步更新查询用的表（论点、发言、立场、决策卡）。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

from .util import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY, user_id TEXT, mode TEXT, status TEXT, category TEXT,
                dilemma TEXT, leaning TEXT, mbti TEXT, preset_id TEXT,
                reasons TEXT, questions TEXT, facts TEXT, team TEXT, card TEXT, options TEXT,
                created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS events(
                session_id TEXT, seq INTEGER, type TEXT, data TEXT, ts TEXT,
                PRIMARY KEY(session_id, seq));
CREATE TABLE IF NOT EXISTS utterances(
                session_id TEXT, id TEXT, round INTEGER, kind TEXT, persona_id TEXT, speech TEXT,
                claim_ids TEXT, attempts INTEGER, persona_flag INTEGER, ts TEXT,
                PRIMARY KEY(session_id, id));
CREATE TABLE IF NOT EXISTS claims(
                session_id TEXT, id TEXT, round INTEGER, persona_id TEXT, type TEXT, text TEXT,
                reason_id TEXT, targets TEXT, direction TEXT, sources TEXT, echoed_by TEXT, ts TEXT,
                PRIMARY KEY(session_id, id));
CREATE TABLE IF NOT EXISTS stances(
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, round INTEGER, persona_id TEXT,
                position TEXT, confidence REAL, persuaded_by TEXT, note TEXT,
                changed INTEGER, justified INTEGER, reverted INTEGER);
CREATE TABLE IF NOT EXISTS feedback(
                session_id TEXT, claim_id TEXT, label TEXT, user_id TEXT, ts TEXT,
                PRIMARY KEY(session_id, claim_id));
CREATE TABLE IF NOT EXISTS experiences(
                id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, framework_id TEXT, persona_id TEXT,
                pattern TEXT, source_session TEXT, source_claim TEXT, source TEXT DEFAULT 'user',
                wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, uses INTEGER DEFAULT 0,
                created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS user_profiles(
                user_id TEXT PRIMARY KEY, mbti TEXT, profile TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS prompt_versions(
                id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id TEXT, version INTEGER, patch TEXT,
                status TEXT, score REAL, baseline_score REAL, rationale TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS evolution_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, layer TEXT, kind TEXT, summary TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS llm_calls(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, session_id TEXT, purpose TEXT, provider TEXT,
                model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms INTEGER, ok INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS preset_runs(
                key TEXT PRIMARY KEY, preset_id TEXT, events TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_exp_cat ON experiences(category);
CREATE INDEX IF NOT EXISTS idx_llm_session ON llm_calls(session_id);
"""

_JSON = {
    "reasons", "questions", "facts", "team", "card", "options", "data", "claim_ids",
    "sources", "echoed_by", "profile", "detail", "events",
}


def _dump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k, v in d.items():
        if k in _JSON and isinstance(v, str):
            d[k] = json.loads(v)
    return d


def _num(item_id: str) -> int:
    return int(item_id[1:])


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(experiences)").fetchall()}
        if "source" not in cols:  # 旧库升级
            self._conn.execute("ALTER TABLE experiences ADD COLUMN source TEXT DEFAULT 'user'")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._lock:
            return _row(self._conn.execute(sql, params).fetchone())

    # ---------- sessions ----------
    def create_session(self, s: dict) -> dict:
        ts = now_iso()
        self._exec(
            "INSERT INTO sessions(id,user_id,mode,status,category,dilemma,leaning,mbti,preset_id,"
            "reasons,questions,facts,team,card,options,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                s["id"], s.get("user_id"), s["mode"], s["status"], s.get("category"), s["dilemma"],
                s.get("leaning", ""), s.get("mbti"), s.get("preset_id"),
                _dump(s.get("reasons", [])), _dump(s.get("questions", [])), _dump(s.get("facts", [])),
                _dump(s.get("team", [])), _dump(s.get("card")), _dump(s.get("options", {})), ts, ts,
            ),
        )
        return self.get_session(s["id"])

    def get_session(self, sid: str) -> dict | None:
        return self._one("SELECT * FROM sessions WHERE id=?", (sid,))

    def update_session(self, sid: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            vals.append(_dump(v) if k in _JSON else v)
        cols.append("updated_at=?")
        vals.append(now_iso())
        self._exec(f"UPDATE sessions SET {','.join(cols)} WHERE id=?", (*vals, sid))

    def interrupt_running(self) -> int:
        """服务重启时，把上次没跑完的会话标记为失败。"""
        cur = self._exec("UPDATE sessions SET status='failed' WHERE status='running'")
        return cur.rowcount

    # ---------- events ----------
    def append_event(self, sid: str, type_: str, data: dict) -> dict:
        ts = now_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS n FROM events WHERE session_id=?", (sid,)
            ).fetchone()
            seq = row["n"]
            self._conn.execute(
                "INSERT INTO events(session_id,seq,type,data,ts) VALUES(?,?,?,?,?)",
                (sid, seq, type_, _dump(data), ts),
            )
            self._project(sid, type_, data, ts)
        return {"seq": seq, "type": type_, "data": data, "ts": ts}

    def events(self, sid: str, after: int = 0, types: tuple[str, ...] | None = None) -> list[dict]:
        rows = self._all(
            "SELECT seq,type,data,ts FROM events WHERE session_id=? AND seq>? ORDER BY seq",
            (sid, after),
        )
        return [r for r in rows if not types or r["type"] in types]

    def last_seq(self, sid: str) -> int:
        r = self._one("SELECT COALESCE(MAX(seq),0) AS n FROM events WHERE session_id=?", (sid,))
        return r["n"]

    def _insert_claim(self, sid: str, c: dict, ts: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO claims(session_id,id,round,persona_id,type,text,reason_id,targets,"
            "direction,sources,echoed_by,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, c["id"], c.get("round", 0), c["persona_id"], c["type"], c["text"],
                c.get("reason_id"), c.get("targets"), c.get("direction", "neutral"),
                _dump(c.get("sources", [])), _dump(c.get("echoed_by", [])), ts,
            ),
        )

    def _project(self, sid: str, type_: str, d: dict, ts: str) -> None:
        c = self._conn
        if type_ == "team.selected":
            c.execute("UPDATE sessions SET team=?, updated_at=? WHERE id=?", (_dump(d["team"]), ts, sid))
        elif type_ == "status":
            c.execute("UPDATE sessions SET status=?, updated_at=? WHERE id=?", (d["status"], ts, sid))
        elif type_ in ("card.ready", "card.updated"):
            c.execute("UPDATE sessions SET card=?, updated_at=? WHERE id=?", (_dump(d["card"]), ts, sid))
        elif type_ == "user.interjected":
            self._insert_claim(sid, d["claim"], ts)
        elif type_ == "utterance.completed":
            c.execute(
                "INSERT OR REPLACE INTO utterances(session_id,id,round,kind,persona_id,speech,claim_ids,"
                "attempts,persona_flag,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    sid, d["id"], d["round"], d.get("kind"), d["persona_id"], d["speech"],
                    _dump([x["id"] for x in d["claims"]]), d.get("attempts", 1),
                    int(bool(d.get("persona_flag"))), ts,
                ),
            )
            for cl in d["claims"]:
                self._insert_claim(sid, cl, ts)
            for e in d.get("echoes", []):
                row = c.execute(
                    "SELECT echoed_by FROM claims WHERE session_id=? AND id=?", (sid, e["claim_id"])
                ).fetchone()
                if row:
                    lst = json.loads(row["echoed_by"])
                    if d["persona_id"] not in lst:
                        lst.append(d["persona_id"])
                        c.execute(
                            "UPDATE claims SET echoed_by=? WHERE session_id=? AND id=?",
                            (_dump(lst), sid, e["claim_id"]),
                        )
            st = d.get("stance")
            if st:
                c.execute(
                    "INSERT INTO stances(session_id,round,persona_id,position,confidence,persuaded_by,"
                    "note,changed,justified,reverted) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid, d["round"], d["persona_id"], st["position"], st.get("confidence"),
                        st.get("persuaded_by"), st.get("note", ""), int(bool(st.get("changed"))),
                        int(bool(st.get("justified", True))), int(bool(st.get("reverted"))),
                    ),
                )

    # ---------- projections ----------
    def claims(self, sid: str) -> list[dict]:
        rows = self._all("SELECT * FROM claims WHERE session_id=?", (sid,))
        rows.sort(key=lambda r: _num(r["id"]))
        for r in rows:
            r.pop("session_id", None)
        return rows

    def utterances(self, sid: str) -> list[dict]:
        rows = self._all("SELECT * FROM utterances WHERE session_id=?", (sid,))
        rows.sort(key=lambda r: _num(r["id"]))
        for r in rows:
            r.pop("session_id", None)
            r["persona_flag"] = bool(r["persona_flag"])
        return rows

    def stances(self, sid: str) -> list[dict]:
        rows = self._all("SELECT * FROM stances WHERE session_id=? ORDER BY id", (sid,))
        for r in rows:
            r.pop("session_id", None)
            for k in ("changed", "justified", "reverted"):
                r[k] = bool(r[k])
        return rows

    # ---------- feedback ----------
    def feedback(self, sid: str) -> list[dict]:
        return self._all("SELECT claim_id,label,ts FROM feedback WHERE session_id=?", (sid,))

    def save_feedback(self, sid: str, claim_id: str, label: str, user_id: str | None) -> bool:
        """返回 True 表示这条论点是第一次收到反馈（只有第一次会触发进化，避免重复计数）。"""
        with self._lock:
            old = self._conn.execute(
                "SELECT 1 FROM feedback WHERE session_id=? AND claim_id=?", (sid, claim_id)
            ).fetchone()
            self._conn.execute(
                "INSERT OR REPLACE INTO feedback(session_id,claim_id,label,user_id,ts) VALUES(?,?,?,?,?)",
                (sid, claim_id, label, user_id, now_iso()),
            )
        return old is None

    def labeled_claims(self, persona_id: str, labels: tuple[str, ...], limit: int = 15) -> list[dict]:
        q = ",".join("?" * len(labels))
        return self._all(
            "SELECT c.text, c.type, c.direction, f.label, s.category FROM feedback f "
            "JOIN claims c ON c.session_id=f.session_id AND c.id=f.claim_id "
            "JOIN sessions s ON s.id=f.session_id "
            f"WHERE c.persona_id=? AND f.label IN ({q}) ORDER BY f.ts DESC LIMIT ?",
            (persona_id, *labels, limit),
        )

    # ---------- experiences ----------
    def experiences(self, category: str | None = None, limit: int = 200) -> list[dict]:
        if category:
            rows = self._all("SELECT * FROM experiences WHERE category=?", (category,))
        else:
            rows = self._all("SELECT * FROM experiences")
        for r in rows:
            r["score"] = round((r["wins"] + 1) / (r["wins"] + r["losses"] + 2), 3)
            r["votes"] = r["wins"] + r["losses"]
        rows.sort(key=lambda r: (-r["score"], -r["wins"], r["id"]))
        return rows[:limit]

    def add_experience(self, **e: Any) -> int:
        ts = now_iso()
        cur = self._exec(
            "INSERT INTO experiences(category,framework_id,persona_id,pattern,source_session,source_claim,source,"
            "wins,losses,uses,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,0,0,?,?)",
            (
                e["category"], e.get("framework_id"), e.get("persona_id"), e["pattern"],
                e.get("source_session"), e.get("source_claim"), e.get("source", "user"), e.get("wins", 1), ts, ts,
            ),
        )
        return cur.lastrowid

    def bump_experience(self, exp_id: int, wins: int = 0, losses: int = 0, uses: int = 0) -> None:
        self._exec(
            "UPDATE experiences SET wins=wins+?, losses=losses+?, uses=uses+?, updated_at=? WHERE id=?",
            (wins, losses, uses, now_iso(), exp_id),
        )

    # ---------- user profiles ----------
    def get_profile(self, user_id: str) -> dict | None:
        return self._one("SELECT * FROM user_profiles WHERE user_id=?", (user_id,))

    def save_profile(self, user_id: str, mbti: str | None, profile: dict) -> None:
        self._exec(
            "INSERT OR REPLACE INTO user_profiles(user_id,mbti,profile,updated_at) VALUES(?,?,?,?)",
            (user_id, mbti, _dump(profile), now_iso()),
        )

    # ---------- prompt versions ----------
    def prompt_versions(self, persona_id: str | None = None) -> list[dict]:
        if persona_id:
            return self._all(
                "SELECT * FROM prompt_versions WHERE persona_id=? ORDER BY id DESC", (persona_id,)
            )
        return self._all("SELECT * FROM prompt_versions ORDER BY id DESC")

    def get_prompt_version(self, vid: int) -> dict | None:
        return self._one("SELECT * FROM prompt_versions WHERE id=?", (vid,))

    def add_prompt_version(self, persona_id: str, patch: str, rationale: str) -> dict:
        ts = now_iso()
        with self._lock:
            n = self._conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS n FROM prompt_versions WHERE persona_id=?",
                (persona_id,),
            ).fetchone()["n"]
            cur = self._conn.execute(
                "INSERT INTO prompt_versions(persona_id,version,patch,status,rationale,created_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?)",
                (persona_id, n, patch, "candidate", rationale, ts, ts),
            )
            vid = cur.lastrowid
        return self.get_prompt_version(vid)

    def set_prompt_status(
        self, vid: int, status: str, score: float | None = None, baseline_score: float | None = None
    ) -> None:
        self._exec(
            "UPDATE prompt_versions SET status=?, score=COALESCE(?,score), "
            "baseline_score=COALESCE(?,baseline_score), updated_at=? WHERE id=?",
            (status, score, baseline_score, now_iso(), vid),
        )

    def patch_for(self, persona_id: str, status: str = "active") -> dict | None:
        return self._one(
            "SELECT * FROM prompt_versions WHERE persona_id=? AND status=? ORDER BY id DESC LIMIT 1",
            (persona_id, status),
        )

    # ---------- evolution log ----------
    def log_evolution(self, layer: str, kind: str, summary: str, detail: dict | None = None) -> None:
        self._exec(
            "INSERT INTO evolution_log(ts,layer,kind,summary,detail) VALUES(?,?,?,?,?)",
            (now_iso(), layer, kind, summary, _dump(detail or {})),
        )

    def evolution_log(self, limit: int = 50) -> list[dict]:
        return self._all("SELECT * FROM evolution_log ORDER BY id DESC LIMIT ?", (limit,))

    # ---------- llm calls ----------
    def log_llm_call(self, **c: Any) -> None:
        self._exec(
            "INSERT INTO llm_calls(ts,session_id,purpose,provider,model,prompt_tokens,"
            "completion_tokens,latency_ms,ok,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                now_iso(), c.get("session_id"), c.get("purpose"), c.get("provider"), c.get("model"),
                c.get("prompt_tokens", 0), c.get("completion_tokens", 0), c.get("latency_ms", 0),
                int(c.get("ok", True)), c.get("error"),
            ),
        )

    def llm_usage(self, sid: str | None = None) -> dict:
        where, params = ("WHERE session_id=?", (sid,)) if sid else ("", ())
        return self._one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            f"COALESCE(SUM(1-ok),0) AS failures FROM llm_calls {where}",
            params,
        )

    # ---------- preset cache ----------
    def get_preset_run(self, key: str) -> list[dict] | None:
        r = self._one("SELECT events FROM preset_runs WHERE key=?", (key,))
        return r["events"] if r else None

    def save_preset_run(self, key: str, preset_id: str, events: list[dict]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO preset_runs(key,preset_id,events,created_at) VALUES(?,?,?,?)",
            (key, preset_id, _dump(events), now_iso()),
        )

    def clear_preset_runs(self) -> int:
        return self._exec("DELETE FROM preset_runs").rowcount

    # ---------- stats ----------
    def stats(self) -> dict:
        def n(sql: str) -> int:
            return self._one(sql)["n"]

        return {
            "sessions": n("SELECT COUNT(*) AS n FROM sessions WHERE mode!='eval'"),
            "sessions_completed": n("SELECT COUNT(*) AS n FROM sessions WHERE status='completed' AND mode!='eval'"),
            "claims": n("SELECT COUNT(*) AS n FROM claims WHERE persona_id!='user'"),
            "feedback": n("SELECT COUNT(*) AS n FROM feedback"),
            "feedback_positive": n(
                "SELECT COUNT(*) AS n FROM feedback WHERE label IN ('unexpected','valuable')"
            ),
            "feedback_useless": n("SELECT COUNT(*) AS n FROM feedback WHERE label='useless'"),
            "experiences": n("SELECT COUNT(*) AS n FROM experiences"),
            "prompt_versions_active": n(
                "SELECT COUNT(*) AS n FROM prompt_versions WHERE status='active'"
            ),
            "quality_rejections": n("SELECT COUNT(*) AS n FROM events WHERE type='quality.rejected'"),
        }
