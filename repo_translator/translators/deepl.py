"""DeepL Translate engine (best quality, $5.49/month for 500K chars)."""

import os
import time
import logging
from typing import List

from .base import BaseTranslator

logger = logging.getLogger(__name__)

# DeepL language code mapping
DEEPL_LANG_MAP = {
    "zh": "ZH",
    "ja": "JA",
    "ko": "KO",
    "en": "EN",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "pt": "PT",
    "ru": "RU",
    "ar": "AR",
    "it": "IT",
    "nl": "NL",
    "pl": "PL",
}


class DeepLTranslator(BaseTranslator):
    """DeepL Translate via official API."""

    def __init__(self, source_lang: str, target_lang: str, api_key: str = None):
        super().__init__(source_lang, target_lang)
        self.api_key = api_key or os.environ.get("DEEPL_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "DeepL API key required. Set DEEPL_API_KEY env var or pass api_key parameter.\n"
                "Get one at: https://www.deepl.com/pro-api"
            )
        self._base_url = "https://api-free.deepl.com/v2/translate"
        # Use paid endpoint if key doesn't end with :fx
        if not self.api_key.endswith(":fx"):
            self._base_url = "https://api.deepl.com/v2/translate"

    @property
    def name(self) -> str:
        return "DeepL"

    @property
    def max_chars_per_request(self) -> int:
        return 50000  # DeepL handles large texts well

    def _get_lang_code(self, lang: str) -> str:
        return DEEPL_LANG_MAP.get(lang, lang.upper())

    def translate_text(self, text: str) -> str:
        if not text.strip():
            return text

        import requests

        src = self._get_lang_code(self.source_lang)
        dest = self._get_lang_code(self.target_lang)

        chunks = self._chunk_text(text, self.max_chars_per_request)
        translated_chunks = []

        for chunk in chunks:
            if not chunk.strip():
                translated_chunks.append(chunk)
                continue

            try:
                resp = requests.post(
                    self._base_url,
                    data={
                        "auth_key": self.api_key,
                        "text": chunk,
                        "source_lang": src,
                        "target_lang": dest,
                        "preserve_formatting": "1",
                        "split_sentences": "0",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                result = resp.json()
                translated_chunks.append(result["translations"][0]["text"])
            except Exception as e:
                logger.warning(f"DeepL translation failed: {e}")
                translated_chunks.append(chunk)

            time.sleep(0.1)

        return "\n".join(translated_chunks)

    def translate_batch(self, texts: List[str]) -> List[str]:
        """Batch translate — DeepL supports multiple texts in one request."""
        import requests

        src = self._get_lang_code(self.source_lang)
        dest = self._get_lang_code(self.target_lang)

        results = []
        # Batch in groups of 50
        batch_size = 50
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            non_empty = [(j, t) for j, t in enumerate(batch) if t.strip()]

            if not non_empty:
                results.extend(batch)
                continue

            try:
                data = {
                    "auth_key": self.api_key,
                    "source_lang": src,
                    "target_lang": dest,
                    "preserve_formatting": "1",
                    "split_sentences": "0",
                }
                for idx, (_, text) in enumerate(non_empty):
                    data[f"text[{idx}]"] = text

                resp = requests.post(self._base_url, data=data, timeout=60)
                resp.raise_for_status()
                translations = resp.json()["translations"]

                batch_results = list(batch)
                for (orig_idx, _), trans in zip(non_empty, translations):
                    batch_results[orig_idx] = trans["text"]

                results.extend(batch_results)
            except Exception as e:
                logger.warning(f"DeepL batch failed, falling back to sequential: {e}")
                for text in batch:
                    results.append(self.translate_text(text))

            logger.info(f"  DeepL batch: {min(i + batch_size, len(texts))}/{len(texts)}")
            time.sleep(0.5)

        return results
