from __future__ import annotations

import os

from google.adk.models.lite_llm import LiteLlm


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _litellm_model(model_name: str, *, default_base_url: str | None = None) -> LiteLlm:
    kwargs = {}
    api_key = _first_env(
        "OPENSAGE_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_API",
        "GPT_LITELLM_API_KEY",
        "ANTHROPIC_API_KEY",
    )
    base_url = _first_env("OPENSAGE_BASE_URL", "GPT_LITELLM_BASE_URL") or default_base_url
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return LiteLlm(model=model_name, **kwargs)


_DEFAULT_MODEL_NAME = os.environ.get("OPENSAGE_MODEL", "openai/gpt-4o-mini")
DEFAULT_MODEL = _litellm_model(_DEFAULT_MODEL_NAME)

CLAUDE_MODEL = _litellm_model(
    os.environ.get("CLAUDE_MODEL", "anthropic/claude-opus-4-1"),
    default_base_url=os.environ.get("CLAUDE_LITELLM_BASE_URL"),
)

models = [DEFAULT_MODEL, CLAUDE_MODEL]
