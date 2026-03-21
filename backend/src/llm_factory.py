"""Provider-agnostic chat model factory for the backend graph."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any

_DEFAULT_OLLAMA_BASE_URL = "http://100.96.111.98:11434"
_SUPPORTED_PROVIDERS = {
    "ollama",
    "openai",
    "anthropic",
    "gemini",
    "openai_compatible",
}
_PROVIDER_ALIASES = {
    "chatgpt": "openai",
    "open_ai": "openai",
    "gpt": "openai",
    "claude": "anthropic",
    "google": "gemini",
    "google_genai": "gemini",
    "google_generative_ai": "gemini",
    "openai-compatible": "openai_compatible",
    "openai_compatible": "openai_compatible",
    "local": "openai_compatible",
    "localai": "openai_compatible",
    "lmstudio": "openai_compatible",
    "lm_studio": "openai_compatible",
    "vllm": "openai_compatible",
}


@dataclass(frozen=True)
class LLMConfig:
    """Resolved runtime configuration for a single chat model instance."""

    provider: str
    model: str
    temperature: float
    base_url: str | None = None
    api_key: str | None = None
    num_ctx: int | None = None
    num_predict: int | None = None
    max_tokens: int | None = None
    max_retries: int | None = None
    request_timeout: float | None = None
    reasoning: bool = False


def normalize_provider(raw_provider: str | None) -> str:
    """Normalize provider aliases into canonical provider ids."""
    provider = (raw_provider or "ollama").strip().lower().replace("-", "_")
    provider = _PROVIDER_ALIASES.get(provider, provider)
    if provider not in _SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(_SUPPORTED_PROVIDERS))
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{raw_provider}'. "
            f"Supported values: {supported}"
        )
    return provider


def _env(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value is not None and value != "":
            return value
    return default


def _int_env(*keys: str, default: int | None = None) -> int | None:
    value = _env(*keys)
    if value is None:
        return default
    return int(value)


def _float_env(*keys: str, default: float | None = None) -> float | None:
    value = _env(*keys)
    if value is None:
        return default
    return float(value)


def _provider_model_keys(provider: str, *, reasoning: bool) -> tuple[str, ...]:
    if provider == "ollama":
        return (
            "OLLAMA_ANSWER_MODEL" if reasoning else "OLLAMA_TOOL_MODEL",
            "OLLAMA_MODEL",
        )
    if provider == "openai":
        return (
            "OPENAI_ANSWER_MODEL" if reasoning else "OPENAI_TOOL_MODEL",
            "OPENAI_MODEL",
        )
    if provider == "anthropic":
        return (
            "ANTHROPIC_ANSWER_MODEL" if reasoning else "ANTHROPIC_TOOL_MODEL",
            "ANTHROPIC_MODEL",
        )
    if provider == "gemini":
        return (
            "GEMINI_ANSWER_MODEL" if reasoning else "GEMINI_TOOL_MODEL",
            "GOOGLE_ANSWER_MODEL" if reasoning else "GOOGLE_TOOL_MODEL",
            "GEMINI_MODEL",
            "GOOGLE_MODEL",
        )
    return (
        "OPENAI_COMPATIBLE_ANSWER_MODEL"
        if reasoning
        else "OPENAI_COMPATIBLE_TOOL_MODEL",
        "OPENAI_COMPATIBLE_MODEL",
    )


def _require_model(provider: str, model: str | None) -> str:
    if model:
        return model
    raise ValueError(
        "No model configured for provider "
        f"'{provider}'. Set LLM_MODEL or a provider-specific model env var."
    )


def resolve_llm_config(reasoning: bool = False) -> LLMConfig:
    """Resolve provider-agnostic env vars into a concrete model config."""
    provider = normalize_provider(os.getenv("LLM_PROVIDER", "ollama"))
    model = _require_model(
        provider,
        _env(
            "LLM_ANSWER_MODEL" if reasoning else "LLM_TOOL_MODEL",
            "LLM_MODEL",
            *_provider_model_keys(provider, reasoning=reasoning),
            default="qwen3.5:9b" if provider == "ollama" else None,
        ),
    )
    temperature = _float_env("LLM_TEMPERATURE", default=0.0)
    max_tokens = _int_env(
        "LLM_ANSWER_MAX_TOKENS" if reasoning else "LLM_MAX_TOKENS",
        default=None,
    )
    request_timeout = _float_env(
        "LLM_ANSWER_REQUEST_TIMEOUT" if reasoning else "LLM_REQUEST_TIMEOUT",
        "LLM_REQUEST_TIMEOUT",
        default=300.0 if reasoning else 60.0,
    )

    if provider == "ollama":
        default_num_ctx = 49152 if reasoning else 32768
        default_num_predict = 4096 if reasoning else 1024
        return LLMConfig(
            provider=provider,
            model=model,
            temperature=temperature or 0.0,
            base_url=_env("LLM_BASE_URL", "OLLAMA_BASE_URL", default=_DEFAULT_OLLAMA_BASE_URL),
            num_ctx=_int_env("LLM_NUM_CTX", "OLLAMA_NUM_CTX", default=default_num_ctx),
            num_predict=_int_env(
                "LLM_ANSWER_NUM_PREDICT" if reasoning else "LLM_NUM_PREDICT",
                "OLLAMA_ANSWER_NUM_PREDICT" if reasoning else "OLLAMA_NUM_PREDICT",
                "OLLAMA_NUM_PREDICT",
                default=default_num_predict,
            ),
            request_timeout=request_timeout,
            reasoning=reasoning,
        )

    if provider == "openai":
        return LLMConfig(
            provider=provider,
            model=model,
            temperature=temperature or 0.0,
            base_url=_env("LLM_BASE_URL", "OPENAI_BASE_URL"),
            api_key=_env("LLM_API_KEY", "OPENAI_API_KEY"),
            max_tokens=max_tokens,
            max_retries=_int_env("LLM_MAX_RETRIES", "OPENAI_MAX_RETRIES", default=2),
            request_timeout=request_timeout,
            reasoning=reasoning,
        )

    if provider == "anthropic":
        return LLMConfig(
            provider=provider,
            model=model,
            temperature=temperature or 0.0,
            api_key=_env("LLM_API_KEY", "ANTHROPIC_API_KEY"),
            max_tokens=max_tokens,
            max_retries=_int_env("LLM_MAX_RETRIES", "ANTHROPIC_MAX_RETRIES", default=2),
            request_timeout=request_timeout,
            reasoning=reasoning,
        )

    if provider == "gemini":
        return LLMConfig(
            provider=provider,
            model=model,
            temperature=temperature if temperature is not None else 0.0,
            api_key=_env("LLM_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"),
            max_tokens=max_tokens,
            max_retries=_int_env("LLM_MAX_RETRIES", "GEMINI_MAX_RETRIES", default=0),
            request_timeout=request_timeout,
            reasoning=reasoning,
        )

    return LLMConfig(
        provider=provider,
        model=model,
        temperature=temperature or 0.0,
        base_url=_env("LLM_BASE_URL", "OPENAI_COMPATIBLE_BASE_URL"),
        api_key=_env(
            "LLM_API_KEY",
            "OPENAI_API_KEY",
            "OPENAI_COMPATIBLE_API_KEY",
            default="not-needed",
        ),
        max_tokens=max_tokens,
        max_retries=_int_env(
            "LLM_MAX_RETRIES",
            "OPENAI_COMPATIBLE_MAX_RETRIES",
            default=2,
        ),
        request_timeout=request_timeout,
        reasoning=reasoning,
    )


def _import_class(module_name: str, class_name: str, package_name: str) -> Any:
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RuntimeError(
            f"Provider package '{package_name}' is not installed. "
            f"Install it with: pip3 install {package_name}"
        ) from exc
    return getattr(module, class_name)


def _build_ollama_model(config: LLMConfig):
    chat_cls = _import_class("langchain_ollama", "ChatOllama", "langchain-ollama")
    kwargs: dict[str, Any] = {
        "base_url": config.base_url,
        "model": config.model,
        "temperature": config.temperature,
        "reasoning": config.reasoning,
    }
    if config.num_ctx is not None:
        kwargs["num_ctx"] = config.num_ctx
    if config.num_predict is not None:
        kwargs["num_predict"] = config.num_predict
    if config.request_timeout is not None:
        kwargs["client_kwargs"] = {"timeout": config.request_timeout}
    return chat_cls(**kwargs)


def _build_openai_model(config: LLMConfig):
    chat_cls = _import_class("langchain_openai", "ChatOpenAI", "langchain-openai")
    kwargs: dict[str, Any] = {
        "model": config.model,
        "temperature": config.temperature,
    }
    if config.api_key:
        kwargs["api_key"] = config.api_key
    if config.base_url:
        kwargs["base_url"] = config.base_url
    if config.max_tokens is not None:
        kwargs["max_tokens"] = config.max_tokens
    if config.max_retries is not None:
        kwargs["max_retries"] = config.max_retries
    if config.request_timeout is not None:
        kwargs["timeout"] = config.request_timeout
    return chat_cls(**kwargs)


def _build_anthropic_model(config: LLMConfig):
    chat_cls = _import_class(
        "langchain_anthropic", "ChatAnthropic", "langchain-anthropic"
    )
    kwargs: dict[str, Any] = {
        "model": config.model,
        "temperature": config.temperature,
    }
    if config.api_key:
        kwargs["api_key"] = config.api_key
    if config.max_tokens is not None:
        kwargs["max_tokens"] = config.max_tokens
    if config.max_retries is not None:
        kwargs["max_retries"] = config.max_retries
    if config.request_timeout is not None:
        kwargs["timeout"] = config.request_timeout
    return chat_cls(**kwargs)


def _build_gemini_model(config: LLMConfig):
    chat_cls = _import_class(
        "langchain_google_genai",
        "ChatGoogleGenerativeAI",
        "langchain-google-genai",
    )
    kwargs: dict[str, Any] = {
        "model": config.model,
        "temperature": config.temperature,
    }
    if config.api_key:
        kwargs["api_key"] = config.api_key
    if config.max_tokens is not None:
        kwargs["max_tokens"] = config.max_tokens
    if config.max_retries is not None:
        kwargs["retries"] = config.max_retries
    if config.request_timeout is not None:
        kwargs["request_timeout"] = config.request_timeout
    return chat_cls(**kwargs)


def _build_openai_compatible_model(config: LLMConfig):
    if not config.base_url:
        raise ValueError(
            "LLM_BASE_URL is required when LLM_PROVIDER=openai_compatible."
        )
    return _build_openai_model(config)


def create_chat_model(reasoning: bool = False):
    """Create a chat model for the configured provider."""
    config = resolve_llm_config(reasoning=reasoning)
    if config.provider == "ollama":
        return _build_ollama_model(config)
    if config.provider == "openai":
        return _build_openai_model(config)
    if config.provider == "anthropic":
        return _build_anthropic_model(config)
    if config.provider == "gemini":
        return _build_gemini_model(config)
    return _build_openai_compatible_model(config)
