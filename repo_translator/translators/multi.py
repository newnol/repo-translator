"""Multi-translator: combine several engines for higher throughput."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

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
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "+".join(t.name for t in self.translators)

    @property
    def max_chars_per_request(self) -> int:
        if not self.translators:
            return super().max_chars_per_request
        return min(t.max_chars_per_request for t in self.translators)

    def _next_translator(self) -> BaseTranslator:
        """Select an engine safely when file workers call us concurrently."""
        with self._lock:
            translator = self.translators[self._index % len(self.translators)]
            self._index += 1
        return translator

    def translate_text(self, text: str) -> str:
        if not self.translators:
            return text

        return self._next_translator().translate_text(text)

    def translate_batch(self, texts: list[str]) -> list[str]:
        """Split a batch across engines and execute their native batches in parallel."""
        if not self.translators or not texts:
            return list(texts)

        assignments: list[list[tuple[int, str]]] = [[] for _ in self.translators]
        with self._lock:
            start = self._index
            self._index += len(texts)

        for offset, text in enumerate(texts):
            assignments[(start + offset) % len(self.translators)].append((offset, text))

        results = list(texts)

        def run_batch(engine_index: int) -> list[tuple[int, str]]:
            assigned = assignments[engine_index]
            if not assigned:
                return []
            positions, batch = zip(*assigned)
            translated = self.translators[engine_index].translate_batch(list(batch))
            if len(translated) != len(batch):
                raise ValueError(
                    f"{self.translators[engine_index].name} returned "
                    f"{len(translated)} results for {len(batch)} inputs"
                )
            return list(zip(positions, translated))

        active = [index for index, assigned in enumerate(assignments) if assigned]
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            for translated_batch in pool.map(run_batch, active):
                for position, translated in translated_batch:
                    results[position] = translated

        return results
