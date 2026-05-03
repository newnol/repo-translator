"""Base translator interface."""

from abc import ABC, abstractmethod
from typing import List, Optional


class BaseTranslator(ABC):
    """Base class for translation engines."""

    def __init__(self, source_lang: str, target_lang: str):
        self.source_lang = source_lang
        self.target_lang = target_lang

    @abstractmethod
    def translate_text(self, text: str) -> str:
        """Translate a single text string."""
        ...

    def translate_batch(self, texts: List[str]) -> List[str]:
        """Translate a batch of texts. Default: sequential."""
        return [self.translate_text(t) for t in texts]

    @property
    @abstractmethod
    def name(self) -> str:
        """Engine name for display."""
        ...

    @property
    def max_chars_per_request(self) -> int:
        """Max characters per single request."""
        return 5000

    def _is_mostly_code(self, text: str) -> bool:
        """Check if text is mostly code (skip translation)."""
        code_indicators = ['{', '}', '()', '=>', ';;', 'import ', 'def ', 'fn ', 'func ']
        lines = text.strip().split('\n')
        code_lines = sum(1 for line in lines if any(ind in line for ind in code_indicators))
        return len(lines) > 3 and code_lines / len(lines) > 0.7

    def _chunk_text(self, text: str, max_chars: int = 4500) -> List[str]:
        """Split text into chunks for translation, respecting line boundaries."""
        if len(text) <= max_chars:
            return [text]

        chunks = []
        current = []
        current_len = 0

        for line in text.split('\n'):
            if current_len + len(line) + 1 > max_chars and current:
                chunks.append('\n'.join(current))
                current = []
                current_len = 0
            current.append(line)
            current_len += len(line) + 1

        if current:
            chunks.append('\n'.join(current))

        return chunks