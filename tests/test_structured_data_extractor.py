"""Unit tests for structured-data extraction (JSON/YAML).

Validates: Requirements 9.1, 9.2, 9.4
"""

from pathlib import Path

import pytest

from repo_translator.extractors.structured_data import StructuredDataExtractor


@pytest.fixture
def ext():
    return StructuredDataExtractor()


class TestStructuredDataExtractor:
    """Values emitted, keys/numbers/bools/null/empty excluded; URL/path/enum excluded; unlocatable skipped."""

    def test_json_string_values_emitted(self, ext):
        """JSON with string values → emitted (kind=json_value)."""
        content = b'{"greeting": "Hello World", "msg": "Good morning"}'
        candidates = ext.extract(Path("data.json"), content)
        texts = [c.segment.source_text for c in candidates]
        assert "Hello World" in texts
        assert "Good morning" in texts
        assert all(c.segment.kind == "json_value" for c in candidates)

    def test_json_keys_not_emitted(self, ext):
        """JSON keys → NOT emitted."""
        content = b'{"greeting": "Hello World"}'
        candidates = ext.extract(Path("data.json"), content)
        texts = [c.segment.source_text for c in candidates]
        assert "greeting" not in texts

    def test_json_numbers_booleans_null_not_emitted(self, ext):
        """JSON numbers/booleans/null → NOT emitted."""
        content = b'{"count": 42, "active": true, "deleted": false, "meta": null}'
        candidates = ext.extract(Path("data.json"), content)
        assert candidates == []

    def test_empty_string_values_not_emitted(self, ext):
        """Empty string values → NOT emitted."""
        content = b'{"name": "", "space": "   "}'
        candidates = ext.extract(Path("data.json"), content)
        assert candidates == []

    def test_url_values_not_emitted(self, ext):
        """URL values (containing '://') → NOT emitted."""
        content = b'{"link": "https://example.com", "api": "http://localhost:8080"}'
        candidates = ext.extract(Path("data.json"), content)
        assert candidates == []

    def test_path_values_not_emitted(self, ext):
        r"""Path-like values (containing '/' or '\') → NOT emitted."""
        content = b'{"dir": "/usr/local/bin", "win": "C:\\\\Users\\\\me"}'
        candidates = ext.extract(Path("data.json"), content)
        assert candidates == []

    def test_enum_like_values_not_emitted(self, ext):
        """Enum-like values (e.g. 'SOME_VALUE', 'my-token.v2') → NOT emitted."""
        content = b'{"status": "SOME_VALUE", "token": "my-token.v2", "id": "abc_def"}'
        candidates = ext.extract(Path("data.json"), content)
        assert candidates == []

    def test_yaml_string_values_emitted(self, ext):
        """YAML string values → emitted (kind=yaml_value)."""
        content = b"title: Hello World\ndescription: A nice description\n"
        candidates = ext.extract(Path("config.yaml"), content)
        texts = [c.segment.source_text for c in candidates]
        assert "Hello World" in texts
        assert "A nice description" in texts
        assert all(c.segment.kind == "yaml_value" for c in candidates)

    def test_nested_values_emitted(self, ext):
        """Nested values in arrays/objects → emitted."""
        content = b'{"items": [{"label": "First item"}, {"label": "Second item"}]}'
        candidates = ext.extract(Path("data.json"), content)
        texts = [c.segment.source_text for c in candidates]
        assert "First item" in texts
        assert "Second item" in texts

    def test_byte_round_trip(self, ext):
        """Byte round-trip: file_bytes[start:end].decode() == source_text."""
        content = b'{"msg": "Hello World"}'
        candidates = ext.extract(Path("data.json"), content)
        for c in candidates:
            seg = c.segment
            assert content[seg.start_byte:seg.end_byte].decode("utf-8") == seg.source_text
