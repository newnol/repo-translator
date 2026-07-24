"""LLM-based translation engines (OpenAI, Anthropic, Ollama)."""

from __future__ import annotations

import logging
import os
import time

from .base import BaseTranslator

logger = logging.getLogger(__name__)


class LLMTranslator(BaseTranslator):
    """Translate using any OpenAI-compatible LLM API."""

    def __init__(
        self,
        source_lang: str,
        target_lang: str,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
    ):
        super().__init__(source_lang, target_lang)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature

        if not self.api_key:
            raise ValueError("API key required. Set OPENAI_API_KEY env var or pass api_key.")

    @property
    def name(self) -> str:
        return f"LLM ({self.model})"

    @property
    def max_chars_per_request(self) -> int:
        return 8000

    def _build_prompt(self, text: str) -> str:
        """Build translation prompt."""
        return f"""Translate the following text from {self.source_lang} to {self.target_lang}.

RULES:
1. Translate ALL human-readable text (comments, strings, documentation, UI text).
2. Do NOT translate: code, variable names, function names, file paths, URLs.
3. Preserve ALL formatting: markdown syntax, code blocks, indentation, line breaks.
4. Preserve ALL HTML tags, template syntax, and special markers.
5. Keep technical terms accurate (API names, library names, protocol names).
6. Output ONLY the translated text, no explanations.

TEXT TO TRANSLATE:
{text}"""

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return text

        import requests

        chunks = self._chunk_text(text, self.max_chars_per_request)
        translated_chunks = []

        for chunk in chunks:
            if not chunk.strip():
                translated_chunks.append(chunk)
                continue

            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a professional translator. Output only the translated text.",
                            },
                            {"role": "user", "content": self._build_prompt(chunk)},
                        ],
                        "temperature": self.temperature,
                        "max_tokens": 4096,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                result = resp.json()
                translated_chunks.append(result["choices"][0]["message"]["content"])
            except Exception as e:
                logger.warning(f"LLM translation failed: {e}")
                translated_chunks.append(chunk)

            time.sleep(0.5)

        return "\n".join(translated_chunks)


class OllamaTranslator(BaseTranslator):
    """Translate using local Ollama (free, private)."""

    def __init__(
        self,
        source_lang: str,
        target_lang: str,
        model: str = "llama3.1",
        base_url: str = "http://localhost:11434",
    ):
        super().__init__(source_lang, target_lang)
        self.model = model
        self.base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        return f"Ollama ({self.model})"

    @property
    def max_chars_per_request(self) -> int:
        return 4000

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return text

        import requests

        prompt = (
            f"Translate the following text from {self.source_lang} to {self.target_lang}. "
            f"Preserve formatting, code blocks, and technical terms. "
            f"Output ONLY the translated text.\n\n{text}"
        )

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("response", text)
        except Exception as e:
            logger.warning(f"Ollama translation failed: {e}")
            return text
