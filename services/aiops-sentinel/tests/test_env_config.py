"""Env-based QwenClient configuration (QWEN_BASE_URL / QWEN_MODEL / QWEN_API_KEY / QWEN_TIMEOUT)."""

import pytest

from app.core import llm
from app.core.llm import QwenClient


class TestDefaults:
    def test_defaults_with_clean_env(self, monkeypatch):
        for var in (
            "QWEN_BASE_URL",
            "QWEN_MODEL",
            "QWEN_API_KEY",
            "QWEN_TIMEOUT",
            "QWEN_TEMPERATURE",
            "QWEN_DISABLE_THINKING",
        ):
            monkeypatch.delenv(var, raising=False)
        client = QwenClient()
        assert client.base_url == "http://127.0.0.1:8000/v1"
        assert client.model == "qwen2.5-7b"
        assert client.api_key == "not-needed"
        assert client.timeout == 15.0
        assert client.temperature == 0.3

    def test_module_defaults_match_spec(self):
        assert llm.DEFAULT_BASE_URL == "http://127.0.0.1:8000/v1"
        assert llm.DEFAULT_TIMEOUT == 15.0
        assert llm.DEFAULT_API_KEY == "not-needed"


class TestEnvOverrides:
    def test_env_values_override_defaults(self, monkeypatch):
        monkeypatch.setenv("QWEN_BASE_URL", "http://127.0.0.1:9999/v1")
        monkeypatch.setenv("QWEN_MODEL", "test-model")
        monkeypatch.setenv("QWEN_API_KEY", "1234")
        monkeypatch.setenv("QWEN_TIMEOUT", "7.5")
        client = QwenClient()
        assert client.base_url == "http://127.0.0.1:9999/v1"
        assert client.model == "test-model"
        assert client.api_key == "1234"
        assert client.timeout == 7.5

    def test_explicit_arguments_win_over_env(self, monkeypatch):
        monkeypatch.setenv("QWEN_BASE_URL", "http://127.0.0.1:9999/v1")
        monkeypatch.setenv("QWEN_TIMEOUT", "7.5")
        client = QwenClient(base_url="http://localhost:8123/v1", timeout=0.5)
        assert client.base_url == "http://localhost:8123/v1"
        assert client.timeout == 0.5


class TestDisableThinking:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("QWEN_DISABLE_THINKING", raising=False)
        assert QwenClient().disable_thinking is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_env_truthy_enables(self, monkeypatch, value):
        monkeypatch.setenv("QWEN_DISABLE_THINKING", value)
        assert QwenClient().disable_thinking is True

    @pytest.mark.parametrize("value", ["0", "false", "", "no"])
    def test_env_falsy_stays_disabled(self, monkeypatch, value):
        monkeypatch.setenv("QWEN_DISABLE_THINKING", value)
        assert QwenClient().disable_thinking is False

    def test_explicit_argument_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("QWEN_DISABLE_THINKING", "1")
        assert QwenClient(disable_thinking=False).disable_thinking is False
        monkeypatch.delenv("QWEN_DISABLE_THINKING")
        assert QwenClient(disable_thinking=True).disable_thinking is True


class TestTemperature:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("QWEN_TEMPERATURE", "0")
        assert QwenClient().temperature == 0.0

    def test_explicit_argument_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("QWEN_TEMPERATURE", "0")
        assert QwenClient(temperature=0.7).temperature == 0.7
