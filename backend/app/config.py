from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
CONTENT_DIR = Path(__file__).resolve().parent / "content"


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v else default


def _float(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v else default


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


@dataclass
class Settings:
    db_path: str = str(BACKEND_DIR / "data" / "perspectives.db")
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    admin_token: str = ""

    # 辩论
    team_size: int = 3
    deep_rounds: int = 3
    quick_rounds: int = 2
    max_retries: int = 2
    min_challenges: int = 3
    speech_limit: int = 320
    dup_hard: float = 0.72  # 字面相似度超过此值直接判重

    # 模型
    force_mock: bool = False
    mock_delay: float = 0.15
    persona_providers: list[str] = field(default_factory=list)
    judge_provider: str = ""
    llm_timeout: float = 90.0
    llm_max_retries: int = 2
    llm_concurrency: int = 6

    # 查证
    search_provider: str = ""
    search_api_key: str = ""

    quick_replay_delay: float = 0.6

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(BACKEND_DIR / ".env")
        s = cls()
        db = os.environ.get("DB_PATH")
        if db:
            s.db_path = db if os.path.isabs(db) else str(BACKEND_DIR / db)
        s.cors_origins = _list("CORS_ORIGINS") or ["*"]
        s.admin_token = os.environ.get("ADMIN_TOKEN", "")
        s.team_size = _int("TEAM_SIZE", s.team_size)
        s.deep_rounds = _int("DEEP_ROUNDS", s.deep_rounds)
        s.quick_rounds = _int("QUICK_ROUNDS", s.quick_rounds)
        s.max_retries = _int("MAX_RETRIES", s.max_retries)
        s.min_challenges = _int("MIN_CHALLENGES", s.min_challenges)
        s.force_mock = _bool("LLM_MOCK")
        s.persona_providers = _list("PERSONA_PROVIDERS")
        s.judge_provider = os.environ.get("JUDGE_PROVIDER", "")
        s.llm_timeout = _float("LLM_TIMEOUT", s.llm_timeout)
        s.llm_concurrency = _int("LLM_CONCURRENCY", s.llm_concurrency)
        s.search_provider = os.environ.get("SEARCH_PROVIDER", "").lower()
        s.search_api_key = os.environ.get("SEARCH_API_KEY", "")
        s.quick_replay_delay = _float("QUICK_REPLAY_DELAY", s.quick_replay_delay)
        return s
