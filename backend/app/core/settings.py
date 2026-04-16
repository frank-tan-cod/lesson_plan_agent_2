"""Application settings shared across the backend."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    MODEL_NAME: str
    INTENT_MODEL_NAME: str
    DEEPSEEK_BASE_URL: str
    DEEPSEEK_API_KEY: str
    SUMMARY_MODEL_NAME: str
    SUMMARY_API_KEY: str
    CHROMA_PERSIST_DIR: str
    CONVERSATION_SUMMARY_COLLECTION: str
    CORS_ALLOW_ORIGINS: tuple[str, ...]
    PUBLIC_BASE_URL: str


_PLACEHOLDER_PATTERNS = (
    re.compile(r"^\{[A-Z0-9_]+\}$"),
    re.compile(r"^\$\{[A-Z0-9_]+\}$"),
    re.compile(r"^<[A-Z0-9_\-]+>$", re.IGNORECASE),
)

_PLACEHOLDER_VALUES = {
    "your-api-key",
    "your-api-key-here",
    "your_api_key",
    "your_api_key_here",
    "replace-me",
    "replace_with_real_key",
    "sk-xxxxxxxxxxxx",
}


def _looks_like_placeholder(value: str) -> bool:
    """Return True when an env value is clearly a template placeholder."""
    candidate = value.strip()
    if not candidate:
        return False
    if candidate.lower() in _PLACEHOLDER_VALUES:
        return True
    return any(pattern.match(candidate) for pattern in _PLACEHOLDER_PATTERNS)


def _normalize_secret(value: str | None) -> str:
    """Collapse empty or placeholder env values to an empty string."""
    candidate = (value or "").strip()
    if not candidate or _looks_like_placeholder(candidate):
        return ""
    return candidate


def require_llm_api_key(feature_name: str = "当前功能") -> str:
    """Return a usable LLM API key or raise a clear configuration error."""
    if settings.DEEPSEEK_API_KEY:
        return settings.DEEPSEEK_API_KEY
    raise RuntimeError(
        f"{feature_name}缺少有效的模型 API Key，请在 .env 中设置 `DEEPSEEK_API_KEY` 或 `SILICONFLOW_API_KEY`，"
        "并确认不是示例值或占位符。"
    )


def _build_settings() -> Settings:
    model_name = os.getenv("MODEL_NAME", "deepseek-ai/DeepSeek-V3.2")
    intent_model_name = os.getenv("INTENT_MODEL_NAME", model_name)
    base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("BASE_URL", "https://api.siliconflow.cn/v1")
    api_key = _normalize_secret(os.getenv("DEEPSEEK_API_KEY")) or _normalize_secret(os.getenv("SILICONFLOW_API_KEY"))
    summary_model_name = os.getenv("SUMMARY_MODEL_NAME", "deepseek-ai/DeepSeek-R1")
    summary_api_key = _normalize_secret(os.getenv("SUMMARY_API_KEY", ""))
    project_root = Path(__file__).resolve().parents[3]
    chroma_persist_dir = os.getenv("CHROMA_PERSIST_DIR", str(project_root / "chroma_data"))
    summary_collection = os.getenv("CONVERSATION_SUMMARY_COLLECTION", "conversation_summaries")
    raw_cors_origins = os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
    )
    cors_allow_origins = tuple(item.strip() for item in raw_cors_origins.split(",") if item.strip())
    public_base_url = (os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000") or "http://127.0.0.1:8000").strip().rstrip("/")

    return Settings(
        MODEL_NAME=model_name,
        INTENT_MODEL_NAME=intent_model_name,
        DEEPSEEK_BASE_URL=base_url,
        DEEPSEEK_API_KEY=api_key or "",
        SUMMARY_MODEL_NAME=summary_model_name,
        SUMMARY_API_KEY=summary_api_key,
        CHROMA_PERSIST_DIR=chroma_persist_dir,
        CONVERSATION_SUMMARY_COLLECTION=summary_collection,
        CORS_ALLOW_ORIGINS=cors_allow_origins,
        PUBLIC_BASE_URL=public_base_url,
    )


settings = _build_settings()

__all__ = ["Settings", "require_llm_api_key", "settings"]
