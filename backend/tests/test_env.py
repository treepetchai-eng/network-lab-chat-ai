"""
tests/test_env.py
=================
Smoke tests for environment configuration and external service connectivity.

Tests:
    test_env_variables_loaded      — Verifies .env variables are present and
                                     match expected mock values.
    test_ollama_api_reachable      — Performs an HTTP GET to the Ollama API
                                     ``/api/tags`` endpoint and asserts a 200
                                     response, confirming LAN reachability.
    test_ollama_model_available    — Verifies the target model (qwen3.5:9b)
                                     is listed in the Ollama registry.

Run with (from project root):
    pytest tests/test_env.py -v
"""

import os
from pathlib import Path
import sys

import pytest
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env relative to the project root (parent of tests/)
# ---------------------------------------------------------------------------

_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm_factory import normalize_provider

_LLM_PROVIDER = normalize_provider(os.getenv("LLM_PROVIDER", "ollama"))
_OLLAMA_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    os.getenv("OLLAMA_BASE_URL", "http://100.96.111.98:11434"),
)
_REQUEST_TIMEOUT = 5  # seconds


# ---------------------------------------------------------------------------
# Environment Variable Tests
# ---------------------------------------------------------------------------


class TestEnvironmentVariables:
    """Validate that all required .env variables are loaded correctly."""

    def test_router_user_is_set(self) -> None:
        """ROUTER_USER must be non-empty."""
        value = os.getenv("ROUTER_USER")
        assert value is not None, "ROUTER_USER is missing from .env"
        assert value != "", "ROUTER_USER must not be an empty string"

    def test_router_user_expected_value(self) -> None:
        """ROUTER_USER must match the mock value defined in .env."""
        assert os.getenv("ROUTER_USER") == "admin"

    def test_router_pass_is_set(self) -> None:
        """ROUTER_PASS must be non-empty."""
        value = os.getenv("ROUTER_PASS")
        assert value is not None, "ROUTER_PASS is missing from .env"
        assert value != "", "ROUTER_PASS must not be an empty string"

    def test_router_pass_expected_value(self) -> None:
        """ROUTER_PASS must match the mock value defined in .env."""
        assert os.getenv("ROUTER_PASS") == "admin1234"

    def test_llm_provider_is_supported(self) -> None:
        """LLM_PROVIDER must resolve to a supported provider."""
        assert _LLM_PROVIDER in {
            "ollama",
            "openai",
            "anthropic",
            "gemini",
            "openai_compatible",
        }

    def test_selected_provider_has_minimum_config(self) -> None:
        """Selected provider must have the minimum required env vars."""
        model = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL")
        assert model, "LLM_MODEL is missing from .env"

        if _LLM_PROVIDER == "ollama":
            value = os.getenv("LLM_BASE_URL") or os.getenv("OLLAMA_BASE_URL")
            assert value, "LLM_BASE_URL is missing from .env"
            assert value.startswith("http"), (
                f"LLM_BASE_URL must start with http/https, got: {value}"
            )
        elif _LLM_PROVIDER == "openai_compatible":
            value = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_COMPATIBLE_BASE_URL")
            assert value, "LLM_BASE_URL is required for openai_compatible"
            assert value.startswith("http"), (
                f"LLM_BASE_URL must start with http/https, got: {value}"
            )
        elif _LLM_PROVIDER == "openai":
            assert os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        elif _LLM_PROVIDER == "anthropic":
            assert os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        elif _LLM_PROVIDER == "gemini":
            assert (
                os.getenv("LLM_API_KEY")
                or os.getenv("GOOGLE_API_KEY")
                or os.getenv("GEMINI_API_KEY")
            )


# ---------------------------------------------------------------------------
# Ollama API Connectivity Tests
# ---------------------------------------------------------------------------


class TestOllamaConnectivity:
    """Validate HTTP reachability and model availability of the Ollama server."""

    def test_ollama_api_reachable(self) -> None:
        """GET /api/tags must return HTTP 200."""
        if _LLM_PROVIDER != "ollama":
            pytest.skip("Selected provider is not Ollama.")
        url = f"{_OLLAMA_BASE_URL}/api/tags"
        try:
            response = requests.get(url, timeout=_REQUEST_TIMEOUT)
        except requests.exceptions.ConnectionError as exc:
            pytest.fail(
                f"Could not reach Ollama API at {url}. "
                f"Check OLLAMA_BASE_URL and LAN connectivity.\nError: {exc}"
            )
        except requests.exceptions.Timeout:
            pytest.fail(
                f"Request to {url} timed out after {_REQUEST_TIMEOUT}s."
            )

        assert response.status_code == 200, (
            f"Ollama API at {url} returned HTTP {response.status_code}, "
            f"expected 200."
        )

    def test_ollama_response_is_json(self) -> None:
        """GET /api/tags must return a valid JSON body."""
        if _LLM_PROVIDER != "ollama":
            pytest.skip("Selected provider is not Ollama.")
        url = f"{_OLLAMA_BASE_URL}/api/tags"
        try:
            response = requests.get(url, timeout=_REQUEST_TIMEOUT)
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"Ollama API did not return valid JSON: {exc}")

        assert isinstance(data, dict), (
            f"Expected a JSON object from {url}, got: {type(data)}"
        )

    def test_ollama_model_available(self) -> None:
        """The target model 'qwen3.5:9b' must appear in the Ollama model list."""
        if _LLM_PROVIDER != "ollama":
            pytest.skip("Selected provider is not Ollama.")
        url = f"{_OLLAMA_BASE_URL}/api/tags"
        target_model = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL") or "qwen3.5:9b"

        try:
            response = requests.get(url, timeout=_REQUEST_TIMEOUT)
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(
                f"Skipping model check — Ollama API unreachable: {exc}"
            )

        models: list = data.get("models", [])
        model_names: list[str] = [m.get("name", "") for m in models]

        assert any(target_model in name for name in model_names), (
            f"Model '{target_model}' was not found in Ollama.\n"
            f"Available models: {model_names}\n"
            f"Pull it with: ollama pull {target_model}"
        )
