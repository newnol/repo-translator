"""MyMemory translator (free tier: 100K chars/day, no API key required)."""

import logging
import time

from .base import BaseTranslator

logger = logging.getLogger(__name__)


class MyMemory(BaseTranslator):
    """MyMemory API — free tier: 100K chars/day, ~1 req/s. Get free key at https://mymemory.translated.net/doc/spec.php"""

    def __init__(self, source_lang: str, target_lang: str, api_key: str = ""):
        super().__init__(source_lang, target_lang)
        self.api_key = api_key

    @property
    def name(self) -> str:
        return "MyMemory"

    @property
    def max_chars_per_request(self) -> int:
        return 500

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return text

        import requests

        chunks = self._chunk_text(text, self.max_chars_per_request)
        translated = []
        for chunk in chunks:
            if not chunk.strip():
                translated.append(chunk)
                continue

            params = {"q": chunk, "langpair": f"{self.source_lang}|{self.target_lang}"}
            if self.api_key:
                params["key"] = self.api_key

            try:
                r = requests.get("https://api.mymemory.translated.net/get", params=params, timeout=15)
                body = r.json()
                translated.append(body["responseData"]["translatedText"])
            except Exception as e:
                logger.warning(f"MyMemory failed: {e}")
                translated.append(chunk)

            # Rate limit: ~1 req/s without key, ~5 req/s with key
            time.sleep(0.3 if self.api_key else 1.0)

        return "\n".join(translated)
