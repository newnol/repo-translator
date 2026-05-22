"""Google Translate engine (free tier: 500K chars/month)."""

import time
import logging
from typing import List

from .base import BaseTranslator

logger = logging.getLogger(__name__)

# Language code mapping for googletrans (unofficial Google client).
LANG_MAP = {
    "zh": "zh-cn",
    "zh-cn": "zh-cn",
    "zh-tw": "zh-tw",
    "he": "iw",
    "jv": "jv",
}

# deep-translator is stricter about some Google language aliases/casing.
DEEP_TRANSLATOR_LANG_MAP = {
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-CN": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-TW": "zh-TW",
    "he": "iw",
}


class GoogleTranslator(BaseTranslator):
    """Google Translate via googletrans library (unofficial, free)."""

    def __init__(self, source_lang: str, target_lang: str):
        super().__init__(source_lang, target_lang)
        self._translator = None
        self._init_translator()

    def _init_translator(self):
        """Lazy init googletrans."""
        try:
            from googletrans import Translator

            self._translator = Translator()
        except ImportError:
            raise ImportError("googletrans not installed. Run: pip install googletrans==4.0.0-rc1")

    def _get_lang_code(self, lang: str) -> str:
        return LANG_MAP.get(lang, lang)

    @property
    def name(self) -> str:
        return "Google Translate"

    @property
    def max_chars_per_request(self) -> int:
        return 5000

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return text

        src = self._get_lang_code(self.source_lang)
        dest = self._get_lang_code(self.target_lang)

        chunks = self._chunk_text(text, self.max_chars_per_request)
        translated_chunks = []

        for chunk in chunks:
            if not chunk.strip():
                translated_chunks.append(chunk)
                continue

            try:
                result = self._translator.translate(chunk, src=src, dest=dest)
                translated_chunks.append(result.text)
            except Exception as e:
                logger.warning(f"Google Translate failed for chunk: {e}")
                translated_chunks.append(chunk)  # keep original on failure

            # Rate limiting
            time.sleep(0.3)

        return "\n".join(translated_chunks)

    def translate_batch(self, texts: List[str]) -> List[str]:
        """Batch translate with rate limiting."""
        results = []
        for i, text in enumerate(texts):
            results.append(self.translate_text(text))
            if (i + 1) % 10 == 0:
                logger.info(f"  Translated {i + 1}/{len(texts)} texts")
                time.sleep(1)  # batch rate limit
        return results


class DeepTranslator(BaseTranslator):
    """Google Translate via deep-translator library (more reliable)."""

    def __init__(self, source_lang: str, target_lang: str):
        super().__init__(source_lang, target_lang)
        self._translator = None
        self._init_translator()

    def _init_translator(self):
        try:
            from deep_translator import GoogleTranslator as DTGoogle

            self._translator = DTGoogle(
                source=self._get_lang_code(self.source_lang),
                target=self._get_lang_code(self.target_lang),
            )
        except ImportError:
            raise ImportError("deep-translator not installed. Run: pip install deep-translator")

    def _get_lang_code(self, lang: str) -> str:
        return DEEP_TRANSLATOR_LANG_MAP.get(lang, lang)

    @property
    def name(self) -> str:
        return "Google Translate (deep-translator)"

    @property
    def max_chars_per_request(self) -> int:
        return 4500

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return text

        chunks = self._chunk_text(text, self.max_chars_per_request)
        translated_chunks = []

        for chunk in chunks:
            if not chunk.strip():
                translated_chunks.append(chunk)
                continue

            try:
                result = self._translator.translate(chunk)
                translated_chunks.append(result if result else chunk)
            except Exception as e:
                logger.warning(f"Translation failed: {e}")
                translated_chunks.append(chunk)

            time.sleep(0.2)

        return "\n".join(translated_chunks)
