from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


def test_normalize_provider_aliases():
    from src.llm_factory import normalize_provider

    assert normalize_provider("chatgpt") == "openai"
    assert normalize_provider("claude") == "anthropic"
    assert normalize_provider("google") == "gemini"
    assert normalize_provider("openai-compatible") == "openai_compatible"
    assert normalize_provider("ollama") == "ollama"


def test_resolve_llm_config_keeps_legacy_ollama_fallbacks(monkeypatch):
    from src.llm_factory import resolve_llm_config

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    config = resolve_llm_config()

    assert config.provider == "ollama"
    assert config.model == "qwen3.5:9b"
    assert config.base_url == "http://127.0.0.1:11434"
    assert config.num_ctx == 32768
    assert config.num_predict == 1024


def test_resolve_llm_config_openai_compatible_defaults_api_key(monkeypatch):
    from src.llm_factory import resolve_llm_config

    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_MODEL", "local-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)

    config = resolve_llm_config()

    assert config.provider == "openai_compatible"
    assert config.api_key == "not-needed"
    assert config.base_url == "http://localhost:1234/v1"


def test_resolve_llm_config_gemini_fails_fast_by_default(monkeypatch):
    from src.llm_factory import resolve_llm_config

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-test")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.delenv("LLM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("GEMINI_MAX_RETRIES", raising=False)
    monkeypatch.delenv("LLM_REQUEST_TIMEOUT", raising=False)

    config = resolve_llm_config()

    assert config.provider == "gemini"
    assert config.max_retries == 0
    assert config.request_timeout == 60.0


def test_create_chat_model_dispatches_to_openai_builder(monkeypatch):
    from src import llm_factory

    monkeypatch.setenv("LLM_PROVIDER", "chatgpt")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    monkeypatch.setenv("LLM_API_KEY", "secret")

    def fake_build(config):
        return ("openai", config)

    monkeypatch.setattr(llm_factory, "_build_openai_model", fake_build)

    provider, config = llm_factory.create_chat_model()

    assert provider == "openai"
    assert config.provider == "openai"
    assert config.model == "gpt-test"
    assert config.api_key == "secret"


def test_create_chat_model_rejects_unknown_provider(monkeypatch):
    from src.llm_factory import create_chat_model

    monkeypatch.setenv("LLM_PROVIDER", "mystery")
    monkeypatch.setenv("LLM_MODEL", "whatever")

    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        create_chat_model()
