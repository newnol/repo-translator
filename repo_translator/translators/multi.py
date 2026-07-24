"""Multi-translator: combine several engines for higher throughput."""

import logging

from .base import BaseTranslator

logger = logging.getLogger(__name__)


class MultiTranslator(BaseTranslator):
    """Round-robins across multiple translators to distribute load.

    Each translate_text call picks the next translator in sequence.
    Combine with --workers N for full concurrent throughput.
    """

    def __init__(self, translators: list[BaseTranslator]):
        super().__init__(
            translators[0].source_lang if translators else "",
            translators[0].target_lang if translators else "",
        )
        self.translators = translators
        self._index = 0

    @property
    def name(self) -> str:
        return "+".join(t.name for t in self.translators)

    @property
    def max_chars_per_request(self) -> int:
        return max(t.max_chars_per_request for t in self.translators)

    def translate_text(self, text: str) -> str:
        if not self.translators:
            return text

        t = self.translators[self._index % len(self.translators)]
        self._index += 1
        return t.translate_text(text)

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Batch translate with round-robin per text."""
        return [self.translate_text(t) for t in texts]
