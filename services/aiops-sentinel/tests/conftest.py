import pytest
from unittest.mock import AsyncMock, patch

from app.core.llm import QwenClient


@pytest.fixture
def qwen_client():
    """Return a QwenClient configured for testing (won't actually connect)."""
    return QwenClient(base_url="http://localhost:9999/v1", model="qwen2.5-7b", timeout=0.5)


@pytest.fixture
def mock_chat_completion():
    """Patch AsyncOpenAI.chat.completions.create to return a mock response."""

    def _mock_response(model, messages, temperature):
        mock_choice = AsyncMock()
        mock_choice.message.content = '{"root_cause": "database connection pool exhausted", "confidence": 0.92}'
        mock_completion = AsyncMock()
        mock_completion.choices = [mock_choice]
        return mock_completion

    return AsyncMock(return_value=_mock_response(None, None, None))


@pytest.fixture
def mock_chat_completion_text():
    """Patch for free-text response (not structured JSON)."""

    def _mock_response(model, messages, temperature):
        mock_choice = AsyncMock()
        mock_choice.message.content = "The database connection pool is exhausted due to too many concurrent queries."
        mock_completion = AsyncMock()
        mock_completion.choices = [mock_choice]
        return mock_completion

    return AsyncMock(return_value=_mock_response(None, None, None))
