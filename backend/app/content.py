"""内容库：角色、思维框架、预设难题。内容在 app/content/*.yaml，由产品同学维护。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from .config import CONTENT_DIR

# 赛道：产品只做这两类。career 是主赛道（有联网检索和岗位对照卡），life 是生活烦恼（宿舍关系、换专业、去哪个城市等）。
TRACKS = {"career": "职业选择", "life": "生活烦恼"}
# 细分类别（category）仍保留，用于经验库分类；每个类别归属一个赛道。
CATEGORIES = {
    "job": "求职选岗", "switch": "跳槽转行", "study": "升学考研",
    "campus": "校园生活", "relation": "人际关系", "city": "去向与城市", "other": "其他",
}
CATEGORY_TRACK = {"job": "career", "switch": "career", "study": "career",
                  "campus": "life", "relation": "life", "city": "life", "other": "life"}


def track_of(category: str | None) -> str:
    return CATEGORY_TRACK.get(category or "", "life")
MBTI_TYPES = {a + b + c + d for a in "EI" for b in "SN" for c in "TF" for d in "JP"}


def norm_mbti(v: str | None) -> str | None:
    v = (v or "").strip().upper()
    return v if v in MBTI_TYPES else None


class Content:
    def __init__(self, directory: Path = CONTENT_DIR):
        raw = b""
        data: dict[str, list] = dict()
        for name in ("frameworks", "personas", "presets"):
            b = (directory / f"{name}.yaml").read_bytes()
            raw += b
            data[name] = yaml.safe_load(b) or []
        roles_path = directory / "roles.yaml"
        if roles_path.exists():  # 岗位对照卡改了，快速模式的缓存也要失效
            raw += roles_path.read_bytes()
        self.version = hashlib.sha1(raw).hexdigest()[:8]
        self.frameworks: dict[str, dict] = {f["id"]: f for f in data["frameworks"]}
        self.personas: dict[str, dict] = {}
        for p in data["personas"]:
            if p["framework"] not in self.frameworks:
                raise ValueError(f"persona {p['id']} uses unknown framework {p['framework']}")
            p.setdefault("can_search", False)
            p.setdefault("role", "debater")
            p.setdefault("rank", 50)
            self.personas[p["id"]] = p
        self.presets: dict[str, dict] = {p["id"]: p for p in data["presets"]}
        for p in self.presets.values():
            p["track"] = track_of(p.get("category"))
        self.sages = self._load_sages(directory, self.frameworks)
        self.roles = self._load_roles(directory)

    @staticmethod
    def _load_roles(directory: Path) -> dict[str, dict]:
        """岗位对照卡。和思想家语料一样，没有人核对过（checked_by 为空）的卡不会被使用，也不会出现在接口里。"""
        path = directory / "roles.yaml"
        raw = (yaml.safe_load(path.read_bytes()) or []) if path.exists() else []
        out: dict[str, dict] = {}
        for r in raw:
            if not str(r.get("checked_by") or "").strip():
                continue
            r.setdefault("aliases", [])
            r.setdefault("differs", dict())
            r.setdefault("sources", [])
            out[r["id"]] = r
        return out

    @staticmethod
    def _load_sages(directory: Path, frameworks: dict) -> dict[str, dict]:
        """历史思想家。没有人核对过的原文（checked_by 为空）不会被使用，没有任何已核对原文的思想家不会出场。"""
        path = directory / "sages.yaml"
        raw = (yaml.safe_load(path.read_bytes()) or []) if path.exists() else []
        out: dict[str, dict] = {}
        for s in raw:
            if s["framework"] not in frameworks:
                raise ValueError(f"sage {s['id']} uses unknown framework {s['framework']}")
            quotes = [q for q in s.get("quotes") or [] if str(q.get("checked_by") or "").strip() and q.get("text")]
            if not quotes:
                continue
            s["quotes"] = quotes
            s.update(role="debater", custom=False, sage=True, rank=60, can_search=False, mbti=None)
            out[s["id"]] = s
        return out

    def framework_of(self, persona: dict) -> dict:
        return self.frameworks[persona["framework"]]

    def public_persona(self, p: dict, provider: str | None = None) -> dict:
        fw = self.framework_of(p)
        out = {
            "id": p["id"], "name": p["name"], "mbti": p.get("mbti") or None, "role": p["role"],
            "tagline": p.get("tagline", ""), "look": p.get("look", ""), "avatar": p.get("avatar", ""), "color": p.get("color", ""),
            "can_search": bool(p.get("can_search")), "custom": bool(p.get("custom")),
            "sage": bool(p.get("sage")), "era": p.get("era", ""),
            "framework": {"id": fw["id"], "name": fw["name"], "method": fw["method"]},
        }
        if provider:
            out["provider"] = provider
        return out


def pick_roles(content: "Content", text: str, k: int = 4) -> list[dict]:
    """按用户的话挑出提到的岗位对照卡；只提到一个岗位时，补上和它容易混淆的岗位，方便对比。"""
    hits: list[tuple[int, dict]] = []
    for r in content.roles.values():
        pos = [text.find(n) for n in [r["name"], *r["aliases"]] if n and n in text]
        if pos:
            hits.append((min(pos), r))
    picked = [r for _, r in sorted(hits, key=lambda x: x[0])]
    for r in list(picked):
        for other in r["differs"]:
            if len(picked) >= k:
                break
            if other in content.roles and content.roles[other] not in picked:
                picked.append(content.roles[other])
    return picked[:k]


def role_block(roles: list[dict]) -> str:
    """放进提示词的岗位对照卡文字。"""
    nl = chr(10)
    rows = []
    for r in roles:
        src = "；".join(f"{x.get('title', '')}（{x.get('date', '日期不详')}）" for x in r["sources"]) or "无"
        diff = "；".join(f"和「{other}」的区别：{t}" for other, t in r["differs"].items())
        rows.append(nl.join([
            f"- 【{r['name']}】{r.get('scope', '')}",
            f"  日常工作：{r.get('daily', '')}",
            f"  入门要求：{r.get('entry', '')}",
            f"  常见发展：{r.get('path', '')}",
            f"  {diff}" if diff else "  （没有对比说明）",
            f"  来源：{src}",
        ]))
    return nl.join(rows)


def pick_quotes(sage: dict, text: str, k: int = 3) -> list[dict]:
    """按用户的难题找最相关的几条原文：和问题字面重叠最多的优先；一条都不沾边时，取每个主题各一条，保证有话可引。"""
    from .quality import similarity

    scored = sorted(sage["quotes"], key=lambda q: -similarity(q["meaning"] + " " + " ".join(q.get("topics") or []), text))
    picked = [q for q in scored if similarity(q["meaning"], text) > 0.02][:k]
    seen = {t for q in picked for t in q.get("topics") or []}
    for q in sage["quotes"]:
        if len(picked) >= k:
            break
        if q not in picked and not seen & set(q.get("topics") or []):
            picked.append(q)
            seen |= set(q.get("topics") or [])
    return (picked or sage["quotes"])[:k]


def select_team(content: Content, mbti: str | None, size: int, team_ids: list[str] | None = None,
                avoid: list[str] | None = None) -> list[dict]:
    """按 MBTI 互补挑人：字母差异越多越优先，保证有一位查证员，框架不重复。

    结果是确定的（同样的输入得到同样的队伍），快速模式的缓存依赖这一点。
    """
    if team_ids:
        picked = [content.personas[i] for i in team_ids if i in content.personas]
        if picked:
            return picked
    debaters = sorted((p for p in content.personas.values() if p["role"] == "debater"), key=lambda p: p["rank"])
    mbti = norm_mbti(mbti)
    if mbti:
        def diff(p: dict) -> int:
            return sum(1 for a, b in zip(p["mbti"], mbti) if a != b)

        def tie(p: dict) -> str:  # 差异相同时按固定的伪随机顺序排，让每个角色都有出场机会
            return hashlib.sha1((mbti + p["id"]).encode()).hexdigest()

        ranked = sorted(debaters, key=lambda p: (-diff(p), tie(p)))
    else:
        ranked = debaters
    chosen: list[dict] = []
    taken = set(avoid or [])
    searcher = next((p for p in ranked if p["can_search"] and p["framework"] not in taken), None)
    if searcher:
        chosen.append(searcher)
        taken.add(searcher["framework"])
    for p in ranked:
        if len(chosen) >= size:
            break
        if p in chosen or p["framework"] in taken:
            continue
        taken.add(p["framework"])
        chosen.append(p)
    chosen.sort(key=lambda p: ranked.index(p))
    return chosen[:size]
