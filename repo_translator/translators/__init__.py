"""Translator registry and factory."""

from __future__ import annotations

from typing import Optional

from .base import BaseTranslator
from .deepl import DeepLTranslator
from .google import DeepTranslator, GoogleTranslator
from .libre import LibreTranslate
from .llm import LLMTranslator, OllamaTranslator
from .multi import MultiTranslator
from .mymemory import MyMemory

ENGINES = {
    "google": GoogleTranslator,
    "google-alt": DeepTranslator,
    "deepl": DeepLTranslator,
    "openai": LLMTranslator,
    "ollama": OllamaTranslator,
    "libre": LibreTranslate,
    "mymemory": MyMemory,
}


def _build_single(
    engine: str,
    source_lang: str,
    target_lang: str,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> BaseTranslator:
    """Build a single translator by engine name."""
    engine = engine.strip()

    if engine == "google":
        return GoogleTranslator(source_lang, target_lang)

    elif engine == "google-alt":
        return DeepTranslator(source_lang, target_lang)

    elif engine == "deepl":
        return DeepLTranslator(source_lang, target_lang, api_key=api_key)

    elif engine == "openai":
        return LLMTranslator(
            source_lang,
            target_lang,
            api_key=api_key,
            model=model or "gpt-4o-mini",
            base_url=base_url or "https://api.openai.com/v1",
        )

    elif engine == "ollama":
        return OllamaTranslator(
            source_lang,
            target_lang,
            model=model or "llama3.1",
            base_url=base_url or "http://localhost:11434",
        )

    elif engine == "libre":
        return LibreTranslate(source_lang, target_lang, base_url=base_url, api_key=api_key)

    elif engine == "mymemory":
        return MyMemory(source_lang, target_lang, api_key=api_key)

    else:
        available = ", ".join(ENGINES.keys())
        raise ValueError(f"Unknown engine '{engine}'. Available: {available}")


def get_translator(
    engine: str,
    source_lang: str,
    target_lang: str,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> BaseTranslator:
    """
    Factory function to create a translator.

    Supports comma-separated engine names for multi-translator mode:
      ``--translator "libre,mymemory"``
    Each engine gets the same api_key/base_url. To pass per-engine params,
    also comma-separate them (position-matched):
      ``--translator "libre,openai" --base-url "http://localhost:5001,https://api.openai.com/v1"``

    Args:
        engine: Engine name(s), comma-separated for multi mode
        source_lang: Source language code
        target_lang: Target language code
        api_key: API key (or comma-separated, matched by position)
        model: Model name (or comma-separated)
        base_url: Base URL (or comma-separated)
    """
    parts = [e.strip() for e in engine.lower().split(",") if e.strip()]

    if len(parts) == 1:
        return _build_single(parts[0], source_lang, target_lang, api_key, model, base_url)

    # Multi-translator: build one per engine
    api_keys = [k.strip() for k in (api_key or "").split(",")]
    models = [m.strip() for m in (model or "").split(",")]
    base_urls = [b.strip() for b in (base_url or "").split(",")]

    translators = []
    for i, name in enumerate(parts):
        ak = api_keys[i] if i < len(api_keys) else None
        m = models[i] if i < len(models) else None
        b = base_urls[i] if i < len(base_urls) else None
        translators.append(_build_single(name, source_lang, target_lang, ak, m, b))

    return MultiTranslator(translators)


def list_engines():
    """List all available translation engines."""
    return {
        "google": "Google Translate (free, 500K chars/month)",
        "google-alt": "Google Translate via deep-translator (more reliable)",
        "deepl": "DeepL API (best quality, requires API key)",
        "openai": "OpenAI-compatible LLM (requires API key)",
        "ollama": "Local Ollama (free, requires local setup)",
        "libre": "Self-hosted LibreTranslate (no rate limits, requires Docker)",
        "mymemory": "MyMemory (free 100K chars/day, no API key needed)",
    }
