"""Unified LLM client: one call surface, three interchangeable real backends.

Grok (xAI) speaks the OpenAI Chat Completions wire format, so the same
`openai` SDK client works for both xai and openai providers by swapping
base_url. Anthropic uses tool-use for structured output.

No offline mode: if no API key is configured, construction fails immediately
with a clear, actionable error rather than silently degrading to a fake
response. See src/config.py for how the active provider/key is resolved.
"""

from __future__ import annotations

import json
from typing import Type, TypeVar

from pydantic import BaseModel

from src.config import load_settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMNotConfiguredError(LLMError):
    pass


class LLMClient:
    def __init__(self):
        settings = load_settings()  # read fresh — see config.py docstring
        if not settings.llm_api_key:
            raise LLMNotConfiguredError(
                f"No API key is set for the provider '{settings.llm_provider}'. "
                f"Open Settings in the web application and add a key. You can also set the "
                f"matching *_API_KEY environment variable, or add it to the .env file."
            )
        self.provider = settings.llm_provider
        self.model = settings.llm_model

        if self.provider in ("xai", "openai"):
            from openai import OpenAI

            self._client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
        elif self.provider == "anthropic":
            import anthropic

            self._client = anthropic.Anthropic(api_key=settings.llm_api_key)
        else:
            raise LLMError(f"Unknown provider: {self.provider}")

    def complete_structured(self, system: str, user: str, schema: Type[T]) -> T:
        """Return a validated instance of `schema` from a live LLM call."""
        if self.provider in ("xai", "openai"):
            return self._complete_openai_compatible(system, user, schema)
        return self._complete_anthropic(system, user, schema)

    def _complete_openai_compatible(self, system: str, user: str, schema: Type[T]) -> T:
        tool_name = f"emit_{schema.__name__.lower()}"
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"Emit a validated {schema.__name__}.",
                        "parameters": schema.model_json_schema(),
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            temperature=0,
        )
        call = response.choices[0].message.tool_calls[0]
        return schema.model_validate(json.loads(call.function.arguments))

    def _complete_anthropic(self, system: str, user: str, schema: Type[T]) -> T:
        tool_name = f"emit_{schema.__name__.lower()}"
        response = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": tool_name,
                    "description": f"Emit a validated {schema.__name__}.",
                    "input_schema": schema.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            temperature=0,
        )
        for block in response.content:
            if block.type == "tool_use":
                return schema.model_validate(block.input)
        raise LLMError("Anthropic response contained no tool_use block")
