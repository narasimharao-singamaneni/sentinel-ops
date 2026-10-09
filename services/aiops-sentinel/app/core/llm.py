import json
import re
import os

from openai import APIError, AsyncOpenAI
from httpx import ConnectError

DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL = "qwen2.5-7b"
DEFAULT_API_KEY = "not-needed"
DEFAULT_TIMEOUT = 15.0
TRUTHY = ("1", "true", "yes")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUTHY


class LLMUnavailableException(Exception):
    """Raised when the LLM server is unreachable or times out."""

    def __init__(self, message: str = "LLM server is unavailable", cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class QwenClient:
    """Async client targeting a local Qwen-compatible OpenAI-compatible endpoint.

    Configuration resolution (explicit argument > environment > default):
        QWEN_BASE_URL           (default http://127.0.0.1:8000/v1)
        QWEN_MODEL              (default qwen2.5-7b)
        QWEN_API_KEY            (default not-needed)
        QWEN_TIMEOUT            (default 15.0 seconds)
        QWEN_TEMPERATURE        (default 0.3). 0 = greedy decoding, used by
                                 verification to get reproducible RCA text.
        QWEN_DISABLE_THINKING   (default off). When enabled, requests carry
                                 chat_template_kwargs {"enable_thinking": false}
                                 — required for thinking-by-default Qwen3
                                 servers, where enabled thinking adds minutes
                                 of reasoning tokens to every call.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        api_key: str | None = None,
        disable_thinking: bool | None = None,
        temperature: float | None = None,
    ) -> None:
        self.base_url = base_url or os.getenv("QWEN_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.getenv("QWEN_MODEL", DEFAULT_MODEL)
        self.timeout = float(timeout if timeout is not None else os.getenv("QWEN_TIMEOUT", str(DEFAULT_TIMEOUT)))
        self.api_key = api_key or os.getenv("QWEN_API_KEY", DEFAULT_API_KEY)
        self.temperature = float(
            temperature if temperature is not None else os.getenv("QWEN_TEMPERATURE", "0.3")
        )
        if disable_thinking is None:
            disable_thinking = _truthy(os.getenv("QWEN_DISABLE_THINKING"))
        self.disable_thinking = disable_thinking

        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    async def generate(self, prompt: str) -> str:
        """Send prompt to Qwen and return the generated text.

        Any LLM transport/status failure (connection refused, timeout, 401,
        429, 5xx — anything reported by the OpenAI-compatible SDK) is mapped
        to LLMUnavailableException so callers fall back instead of failing.
        """
        extra_body = (
            {"chat_template_kwargs": {"enable_thinking": False}} if self.disable_thinking else None
        )
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                extra_body=extra_body,
            )
            return response.choices[0].message.content
        except (APIError, ConnectError, OSError) as exc:
            raise LLMUnavailableException(f"LLM server is unavailable: {exc}", cause=exc)

    async def generate_structured(self, prompt: str) -> dict:
        """Send prompt to Qwen, extract JSON block from response, and return parsed dict."""
        text = await self.generate(prompt)
        return _extract_json(text)


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from LLM response text.

    Returns {"root_cause": "Unable to parse AI response", "raw": ...} when the
    response is not parseable JSON — callers treat that as an invalid RCA.
    """
    cleaned = re.sub(r"```(\w+)?\n?(.*?)```", r"\2", text, flags=re.DOTALL).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        json_text = match.group(0)
    else:
        json_text = cleaned
    try:
        return json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return {"root_cause": "Unable to parse AI response", "raw": json_text}
