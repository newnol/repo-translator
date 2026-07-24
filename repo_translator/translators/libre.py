"""LibreTranslate engine (self-hosted, no rate limits)."""

import logging
from typing import List

from .base import BaseTranslator

logger = logging.getLogger(__name__)


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

            payload = {"q": chunk, "source": self.source_lang, "target": self.target_lang}
            if self.api_key:
                payload["api_key"] = self.api_key

            try:
                r = requests.post(f"{self.base_url}/translate", json=payload, timeout=60)
                r.raise_for_status()
                translated.append(r.json()["translatedText"])
            except Exception as e:
                logger.warning(f"LibreTranslate failed: {e}")
                translated.append(chunk)

        return "\n".join(translated)
