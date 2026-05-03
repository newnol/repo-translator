"""Translator registry and factory."""

from typing import Optional

from .base import BaseTranslator
from .google import GoogleTranslator, DeepTranslator
from .deepl import DeepLTranslator
from .llm import LLMTranslator, OllamaTranslator


ENGINES = {
    'google': GoogleTranslator,
    'google-alt': DeepTranslator,
    'deepl': DeepLTranslator,
    'openai': LLMTranslator,
    'ollama': OllamaTranslator,
}


def get_translator(
    engine: str,
    source_lang: str,
    target_lang: str,
    api_key: str = None,
    model: str = None,
    base_url: str = None,
) -> BaseTranslator:
    """
    Factory function to create a translator.

    Args:
        engine: 'google', 'google-alt', 'deepl', 'openai', 'ollama'
        source_lang: Source language code (e.g., 'zh')
        target_lang: Target language code (e.g., 'en')
        api_key: API key for paid engines
        model: Model name for LLM engines
        base_url: Base URL for LLM/Ollama engines
    """
    engine = engine.lower().strip()

    if engine == 'google':
        return GoogleTranslator(source_lang, target_lang)

    elif engine == 'google-alt':
        return DeepTranslator(source_lang, target_lang)

    elif engine == 'deepl':
        return DeepLTranslator(source_lang, target_lang, api_key=api_key)

    elif engine == 'openai':
        return LLMTranslator(
            source_lang, target_lang,
            api_key=api_key,
            model=model or 'gpt-4o-mini',
            base_url=base_url or 'https://api.openai.com/v1',
        )

    elif engine == 'ollama':
        return OllamaTranslator(
            source_lang, target_lang,
            model=model or 'llama3.1',
            base_url=base_url or 'http://localhost:11434',
        )

    else:
        available = ', '.join(ENGINES.keys())
        raise ValueError(f"Unknown engine '{engine}'. Available: {available}")


def list_engines():
    """List all available translation engines."""
    return {
        'google': 'Google Translate (free, 500K chars/month)',
        'google-alt': 'Google Translate via deep-translator (more reliable)',
        'deepl': 'DeepL API (best quality, requires API key)',
        'openai': 'OpenAI-compatible LLM (requires API key)',
        'ollama': 'Local Ollama (free, requires local setup)',
    }
