"""Builds the configured ``LLMProvider`` (FR-129). The caller picks the model (``CLAUDE_MODEL_*``, FR-130)."""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.scripted_provider import ScriptedProvider


class ProviderNotConfigured(Exception):
    """The configured provider cannot run in this process (e.g. no API key). Never carries the key's value."""


def build_provider(settings: Settings, *, transport: Any = None) -> LLMProvider:
    """``transport`` is a test hook (an ``httpx2`` transport); only the adapter may name that type."""
    if settings.llm_provider == "scripted":
        return ScriptedProvider()
    key = settings.anthropic_api_key  # stripped by Settings, so it matches the redactor's literal
    if not key:
        # Config does not require the key in every process (ADR-053 pending); the caller decides what to do.
        raise ProviderNotConfigured("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set in this process")
    from app.llm.anthropic_provider import AnthropicClaudeProvider  # the SDK loads only where it is used

    return AnthropicClaudeProvider(key, base_url=settings.anthropic_base_url, transport=transport)
