from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import ConnectError

from app.core.llm import QwenClient, LLMUnavailableException


class TestQwenClientGenerate:
    """Positive test: client sends prompt to Qwen and parses generated text."""

    @pytest.mark.asyncio
    async def test_generate_returns_text(self, qwen_client, mock_chat_completion_text):
        with patch.object(qwen_client._client.chat.completions, "create", mock_chat_completion_text):
            result = await qwen_client.generate("Analyze this error log")
            assert "connection pool" in result
            assert "exhausted" in result


class TestQwenClientStructured:
    """Positive test: client parses structured JSON from Qwen response."""

    @pytest.mark.asyncio
    async def test_generate_structured_parses_json(self, qwen_client, mock_chat_completion):
        with patch.object(qwen_client._client.chat.completions, "create", mock_chat_completion):
            result = await qwen_client.generate_structured("Root cause analysis prompt")
            assert result["root_cause"] == "database connection pool exhausted"
            assert result["confidence"] == 0.92


class TestQwenClientConnectionError:
    """Negative test: when LLM server is down/times out, raises LLMUnavailableException."""

    @pytest.mark.asyncio
    async def test_generate_raises_on_connect_error(self, qwen_client):
        """Simulate ConnectError (server unreachable / timeout)."""
        error_mock = AsyncMock(side_effect=ConnectError("Connection refused"))
        with patch.object(qwen_client._client.chat.completions, "create", error_mock):
            with pytest.raises(LLMUnavailableException) as exc_info:
                await qwen_client.generate("prompt")
            assert "unavailable" in str(exc_info.value).lower()
            assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_generate_structured_raises_on_connect_error(self, qwen_client):
        """Structured generation also raises LLMUnavailableException on connection failure."""
        error_mock = AsyncMock(side_effect=ConnectError("Connection timed out"))
        with patch.object(qwen_client._client.chat.completions, "create", error_mock):
            with pytest.raises(LLMUnavailableException) as exc_info:
                await qwen_client.generate_structured("prompt")
            assert exc_info.value.cause is not None
