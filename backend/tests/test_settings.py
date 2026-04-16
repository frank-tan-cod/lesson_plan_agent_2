from __future__ import annotations

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

settings_module = importlib.import_module("backend.app.core.settings")


def _make_settings(*, api_key: str = "", summary_api_key: str = "") -> settings_module.Settings:
    return settings_module.Settings(
        MODEL_NAME="deepseek-ai/DeepSeek-V3.2",
        INTENT_MODEL_NAME="deepseek-ai/DeepSeek-V3.2",
        DEEPSEEK_BASE_URL="https://api.siliconflow.cn/v1",
        DEEPSEEK_API_KEY=api_key,
        SUMMARY_MODEL_NAME="deepseek-ai/DeepSeek-R1",
        SUMMARY_API_KEY=summary_api_key,
        CHROMA_PERSIST_DIR="chroma_data",
        CONVERSATION_SUMMARY_COLLECTION="conversation_summaries",
        CORS_ALLOW_ORIGINS=("http://localhost:3000",),
        PUBLIC_BASE_URL="http://127.0.0.1:8000",
    )


def test_normalize_secret_treats_placeholders_as_missing() -> None:
    assert settings_module._normalize_secret("${SILICONFLOW_API_KEY}") == ""
    assert settings_module._normalize_secret("{SILICONFLOW_API_KEY}") == ""
    assert settings_module._normalize_secret("sk-xxxxxxxxxxxx") == ""
    assert settings_module._normalize_secret("  ") == ""


def test_normalize_secret_keeps_real_values() -> None:
    assert settings_module._normalize_secret("sk-real-token") == "sk-real-token"


def test_require_llm_api_key_returns_real_key(monkeypatch) -> None:
    monkeypatch.setattr(settings_module, "settings", _make_settings(api_key="sk-real-token"))

    assert settings_module.require_llm_api_key("编辑器") == "sk-real-token"


def test_require_llm_api_key_raises_clear_error(monkeypatch) -> None:
    monkeypatch.setattr(settings_module, "settings", _make_settings())

    try:
        settings_module.require_llm_api_key("编辑器")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected require_llm_api_key to raise when API key is missing.")

    assert "编辑器缺少有效的模型 API Key" in message
    assert "SILICONFLOW_API_KEY" in message


def test_build_settings_uses_stable_public_base_url(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000/")

    settings = settings_module._build_settings()

    assert settings.PUBLIC_BASE_URL == "http://127.0.0.1:8000"
