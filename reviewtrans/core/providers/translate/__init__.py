from __future__ import annotations

from ...config import ProviderProfile
from .. import ProviderError
from .base import LLMTranslator, TranslateJob, Translator
from .deep import ENGINES as DEEP_ENGINES
from .deep import DeepTranslator
from .llm import (
    AnthropicTranslator,
    GeminiCompatTranslator,
    OpenAICompatTranslator,
    ZEN_BASE_URL,
    ZEN_FREE_MODELS,
    ZenTranslator,
)
from .machine import GoogleTranslator, MicrosoftTranslator
from .opencode_local import OpenCodeLocalTranslator
from .riva import RivaTranslator

_KINDS: dict[str, type[Translator]] = {
    "google": GoogleTranslator,
    "microsoft": MicrosoftTranslator,
    "riva": RivaTranslator,
    "deep": DeepTranslator,
    "openai": OpenAICompatTranslator,
    "zen": ZenTranslator,
    "opencode": OpenCodeLocalTranslator,
    "gemini": GeminiCompatTranslator,
    "anthropic": AnthropicTranslator,
}

SUGGESTED_MODELS = {
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "deepseek-chat"],
    "zen": list(ZEN_FREE_MODELS),
    "opencode": ["big-pickle", "mimo-v2.6-flash-free", "space-bunny-free", "longcat-2.5-preview-free"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-flash-latest"],
    "anthropic": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
}


def create_translator(profile: ProviderProfile, model_override: str = "") -> Translator:
    cls = _KINDS.get(profile.kind)
    if cls is None:
        raise ProviderError(f"Loại provider dịch không hỗ trợ: {profile.kind}")
    return cls(profile, model_override)


__all__ = [
    "DEEP_ENGINES",
    "LLMTranslator",
    "OpenCodeLocalTranslator",
    "SUGGESTED_MODELS",
    "ZEN_BASE_URL",
    "ZEN_FREE_MODELS",
    "TranslateJob",
    "Translator",
    "ZenTranslator",
    "create_translator",
]
