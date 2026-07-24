"""LibreTranslate engine (self-hosted, no rate limits)."""

from __future__ import annotations

from .base import BaseTranslator


class LibreTranslate(BaseTranslator):
    """Self-hosted LibreTranslate. Run: docker run -d -p 5000:5000 libretranslate/libretranslate"""

    def __init__(self, source_lang: str, target_lang: str, base_url: str = "", api_key: str = ""):
        super().__init__(source_lang, target_lang)
        self.base_url = (base_url or "http://localhost:5000").rstrip("/")
        self.api_key = api_key

    @property
    def name(self) -> str:
        return "LibreTranslate"

    @property
    def max_chars_per_request(self) -> int:
        return 5000

    def _request(self, query: str | list[str]) -> str | list[str]:
        import requests

        payload = {"q": query, "source": self.source_lang, "target": self.target_lang}
        if self.api_key:
            payload["api_key"] = self.api_key
        response = requests.post(f"{self.base_url}/translate", json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["translatedText"]

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return text

        chunks = self._chunk_text(text, self.max_chars_per_request)
        translated = []
        for chunk in chunks:
            if not chunk.strip():
                translated.append(chunk)
                continue
            result = self._request(chunk)
            if not isinstance(result, str):
                raise TypeError("LibreTranslate returned a non-string result")
            translated.append(result)

        return "\n".join(translated)

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Use LibreTranslate's array-valued ``q`` API to translate one HTTP batch."""
        if not texts:
            return []

        results = list(texts)
        translatable = [
            (index, text)
            for index, text in enumerate(texts)
            if text.strip() and len(text) <= self.max_chars_per_request
        ]

        # Long entries still use the chunk-aware single-text implementation.
        for index, text in enumerate(texts):
            if text.strip() and len(text) > self.max_chars_per_request:
                results[index] = self.translate_text(text)

        # Keep each request bounded for public instances and low-memory self-hosts.
        for start in range(0, len(translatable), 50):
            batch = translatable[start : start + 50]
            translated = self._request([text for _, text in batch])
            if not isinstance(translated, list) or len(translated) != len(batch):
                raise ValueError(
                    "LibreTranslate returned an invalid batch response "
                    f"({len(translated) if isinstance(translated, list) else 'not a list'} "
                    f"for {len(batch)} inputs)"
                )
            for (index, _), value in zip(batch, translated):
                if not isinstance(value, str):
                    raise TypeError("LibreTranslate batch contained a non-string result")
                results[index] = value

        return results
