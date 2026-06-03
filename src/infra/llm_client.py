"""Thin LLM client over the Anthropic API (judges, monitors, generators).

Usage:
    client = LLMClient.from_env()          # reads ANTHROPIC_API_KEY
    reply = client.chat(model, messages, temperature=0)

Messages follow the OpenAI-style list format (system role supported). The Anthropic
SDK expects the system prompt as a separate top-level argument — this module handles
that extraction transparently so callers don't need to know the difference.

A `MockBackend` lets judges/generators be tested offline without any API key.
The `COTIM_LLM_BACKEND` env var can be set to "mock" to force the mock globally.

Cost knobs (price_per_1k, avg_tokens) come from config/cost.yaml; this module only
handles call transport.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

from .retry import RetryableError, retry


class LLMBackend(ABC):
    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        """Return the assistant reply text."""


class AnthropicBackend(LLMBackend):
    """Anthropic Messages API backend."""

    def __init__(self, api_key: str):
        try:
            import anthropic
        except ImportError as e:
            raise ImportError("anthropic SDK required: pip install anthropic") from e
        self._client = anthropic.Anthropic(api_key=api_key)
        self._anthropic = anthropic

    @retry(max_attempts=5, base=1.0, jitter=True, retry_on=(RetryableError,))
    def chat(self, model: str, messages: list[dict], *, temperature: float = 0.0,
             max_tokens: int = 1024, **kwargs: Any) -> str:
        # Anthropic separates system prompt from the messages list.
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        user_messages = [m for m in messages if m.get("role") != "system"]

        call_kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            messages=user_messages,
            **kwargs,
        )
        if system_parts:
            call_kwargs["system"] = "\n\n".join(system_parts)
        if temperature is not None:
            call_kwargs["temperature"] = temperature

        try:
            resp = self._client.messages.create(**call_kwargs)
            return resp.content[0].text if resp.content else ""
        except self._anthropic.RateLimitError as e:
            raise RetryableError(str(e)) from e
        except self._anthropic.APITimeoutError as e:
            raise RetryableError(str(e)) from e
        except self._anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise RetryableError(str(e)) from e
            raise


class MockBackend(LLMBackend):
    """Offline mock for tests. Returns canned JSON or a configured response."""

    def __init__(self, responses: list[str] | None = None):
        self._responses = list(responses or [])
        self._calls: list[dict] = []
        self._idx = 0

    def chat(self, model: str, messages: list[dict], *, temperature: float = 0.0,
             max_tokens: int = 1024, **kwargs: Any) -> str:
        self._calls.append({"model": model, "messages": messages})
        if self._responses:
            reply = self._responses[self._idx % len(self._responses)]
            self._idx += 1
            return reply
        # Default: echo the last user message in a JSON envelope (useful for judges).
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        return json.dumps({"flag": False, "rationale": f"mock: {last_user[:80]}"})

    @property
    def calls(self) -> list[dict]:
        return self._calls


class LLMClient:
    """Public API: wrap a backend and expose `.chat(...)`."""

    def __init__(self, backend: LLMBackend):
        self._backend = backend

    def chat(self, model: str, messages: list[dict], *, temperature: float = 0.0,
             max_tokens: int = 1024, **kwargs: Any) -> str:
        return self._backend.chat(model, messages, temperature=temperature,
                                  max_tokens=max_tokens, **kwargs)

    @classmethod
    def from_env(cls) -> "LLMClient":
        """Auto-select backend from environment. COTIM_LLM_BACKEND=mock forces mock."""
        if os.environ.get("COTIM_LLM_BACKEND", "").lower() == "mock":
            return cls(MockBackend())
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Set it or use COTIM_LLM_BACKEND=mock for offline testing."
            )
        return cls(AnthropicBackend(api_key))
