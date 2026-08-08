from __future__ import annotations

import os

from google.adk.models.lite_llm import LiteLlm


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _model_reasoning_effort() -> str | None:
    value = os.environ.get("OPENSAGE_REASONING_EFFORT", "").strip()
    if not value or value.lower() in {"0", "false", "no", "off", "none", "default"}:
        return None
    return value


def _litellm_model(model_name: str, *, default_base_url: str | None = None) -> LiteLlm:
    kwargs = {}
    is_kimi_model = model_name.startswith(("kimi/", "moonshot/", "openai/kimi")) or model_name in {
        "kimi-k3",
        "kimi-latest",
    }
    provider_key_env = (
        ("KIMI_API_KEY", "MOONSHOT_API_KEY") if is_kimi_model else ()
    )
    provider_base_url_env = (
        ("KIMI_BASE_URL", "MOONSHOT_BASE_URL") if is_kimi_model else ()
    )
    api_key = _first_env(
        "OPENSAGE_API_KEY",
        *provider_key_env,
        "OPENAI_API_KEY",
        "OPENAI_API",
        "GPT_LITELLM_API_KEY",
        "ANTHROPIC_API_KEY",
    )
    base_url = (
        _first_env(
            "OPENSAGE_BASE_URL",
            *provider_base_url_env,
            "GPT_LITELLM_BASE_URL",
        )
        or default_base_url
    )
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    reasoning_effort = _model_reasoning_effort()
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    return LiteLlm(model=model_name, **kwargs)


_DEFAULT_MODEL_NAME = os.environ.get("OPENSAGE_MODEL", "openai/gpt-5.6")
DEFAULT_MODEL = _litellm_model(_DEFAULT_MODEL_NAME)


models = [DEFAULT_MODEL]
