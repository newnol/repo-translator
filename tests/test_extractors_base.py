"""Unit tests for extractors/base.py: registry, CJK gating, ExtractionReport."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from repo_translator.extractors.base import (
    Candidate,
    ExtractionReport,
    Extractor,
    _registry,
    extract_repo,
    get_extractor,
    register,
)
from repo_translator.segments import Segment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_segment(path: str, text: str, start: int = 0) -> Segment:
    """Build a minimal valid Segment for testing."""
    encoded = text.encode("utf-8")
    end = start + len(encoded)
    return Segment(
        id=hashlib.sha256(f"{path}\x1f{start}\x1f{end}".encode()).hexdigest()[:16],
        path=path,
        kind="comment",
        start_byte=start,
        end_byte=end,
        line=1,
        column=0,
        source_text=text,
        target_text=None,
        file_sha256=hashlib.sha256(encoded).hexdigest(),
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        translation_key=hashlib.sha256(f"{text}\x1fcomment".encode()).hexdigest(),
    )


class FakeExtractor:
    """Extractor returning pre-configured candidates."""

    def __init__(self, candidates_fn=None):
        self._candidates_fn = candidates_fn

    def supports(self, path: Path) -> bool:
        return True

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
        if self._candidates_fn:
            return self._candidates_fn(path, file_bytes)
        return []


class RaisingExtractor:
    """Extractor that always raises."""

    def supports(self, path: Path) -> bool:
        return True

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
        raise RuntimeError("extractor boom")


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure tests don't leak registry state."""
    saved = dict(_registry)
    _registry.clear()
    yield
    _registry.clear()
    _registry.update(saved)


# ---------------------------------------------------------------------------
# 1. get_extractor: None for unregistered; returns registered for registered
# ---------------------------------------------------------------------------


def test_get_extractor_unregistered():
    assert get_extractor(Path("foo.xyz")) is None


def test_get_extractor_registered():
    ext = FakeExtractor()
    register(ext, [".xyz"])
    assert get_extractor(Path("foo.xyz")) is ext


# ---------------------------------------------------------------------------
# 2. register maps multiple suffixes to same extractor
# ---------------------------------------------------------------------------


def test_register_multiple_suffixes():
    ext = FakeExtractor()
    register(ext, [".aaa", ".bbb", ".ccc"])
    assert get_extractor(Path("x.aaa")) is ext
    assert get_extractor(Path("x.bbb")) is ext
    assert get_extractor(Path("x.ccc")) is ext


# ---------------------------------------------------------------------------
# 3. extract_repo with CJK candidates and source_is_cjk=True
# ---------------------------------------------------------------------------


def test_extract_repo_cjk_only_emitted(tmp_path):
    """Only segments containing CJK ideographs pass when source_is_cjk=True."""
    # Use .py suffix so get_translatable_files picks it up with translate_code=True
    f = tmp_path / "hello.py"
    content = "# 你好世界\n# hello world\n"
    f.write_text(content, encoding="utf-8")
    file_bytes = content.encode("utf-8")
    file_sha = hashlib.sha256(file_bytes).hexdigest()

    def make_candidates(path: Path, fb: bytes) -> list[Candidate]:
        # Give non-overlapping byte ranges
        cjk_text = "你好世界"
        ascii_text = "hello world"
        cjk_end = len(cjk_text.encode("utf-8"))
        return [
            Candidate(
                segment=_make_segment(
                    path.as_posix(), cjk_text, start=0
                ),
                translatable=True,
                reason="",
            ),
            Candidate(
                segment=_make_segment(
                    path.as_posix(), ascii_text, start=cjk_end
                ),
                translatable=True,
                reason="",
            ),
        ]

    ext = FakeExtractor(candidates_fn=make_candidates)
    register(ext, [".py"])

    # Fix file_sha256 to match the actual file
    report = ExtractionReport()
    segments = list(
        extract_repo(tmp_path, source_is_cjk=True, translate_code=True, report=report)
    )

    # Only the CJK segment should be emitted
    assert len(segments) == 1
    assert "你好" in segments[0].source_text

    # Non-CJK one should be in skipped
    skipped_reasons = [r for _, _, _, r in report.skipped]
    assert any("CJK" in r for r in skipped_reasons)


# ---------------------------------------------------------------------------
# 4. extract_repo non-CJK candidates appear in report.skipped
# ---------------------------------------------------------------------------


def test_extract_repo_non_cjk_skipped(tmp_path):
    f = tmp_path / "test.py"
    content = "# just ascii\n"
    f.write_text(content, encoding="utf-8")

    def make_candidates(path: Path, fb: bytes) -> list[Candidate]:
        return [
            Candidate(
                segment=_make_segment(
                    path.as_posix(), "just ascii"
                ),
                translatable=True,
                reason="",
            ),
        ]

    ext = FakeExtractor(candidates_fn=make_candidates)
    register(ext, [".py"])

    report = ExtractionReport()
    segments = list(
        extract_repo(tmp_path, source_is_cjk=True, translate_code=True, report=report)
    )

    assert len(segments) == 0
    assert any("CJK" in reason for _, _, _, reason in report.skipped)


# ---------------------------------------------------------------------------
# 5. Non-translatable candidates appear in report.skipped with their reason
# ---------------------------------------------------------------------------


def test_extract_repo_non_translatable_skipped(tmp_path):
    f = tmp_path / "code.py"
    content = "# 变量名\n"
    f.write_text(content, encoding="utf-8")

    def make_candidates(path: Path, fb: bytes) -> list[Candidate]:
        return [
            Candidate(
                segment=_make_segment(
                    path.as_posix(), "变量名"
                ),
                translatable=False,
                reason="identifier-like",
            ),
        ]

    ext = FakeExtractor(candidates_fn=make_candidates)
    register(ext, [".py"])

    report = ExtractionReport()
    segments = list(
        extract_repo(tmp_path, source_is_cjk=True, translate_code=True, report=report)
    )

    assert len(segments) == 0
    assert any("identifier-like" in reason for _, _, _, reason in report.skipped)


# ---------------------------------------------------------------------------
# 6. Unreadable files recorded in report.errors
# ---------------------------------------------------------------------------


def test_extract_repo_unreadable_file(tmp_path):
    f = tmp_path / "locked.py"
    f.write_text("# 内容", encoding="utf-8")
    f.chmod(0o000)

    ext = FakeExtractor()
    register(ext, [".py"])

    report = ExtractionReport()
    segments = list(
        extract_repo(tmp_path, translate_code=True, report=report)
    )

    # Restore permissions for cleanup
    f.chmod(0o644)

    assert len(segments) == 0
    assert any("locked.py" in path for path, _ in report.errors)


# ---------------------------------------------------------------------------
# 7. Fallback to regex when primary extractor raises
# ---------------------------------------------------------------------------


def test_extract_repo_fallback_on_raise(tmp_path, monkeypatch):
    f = tmp_path / "broken.py"
    content = "# 测试内容\n"
    f.write_text(content, encoding="utf-8")

    # Register a raising extractor for .py
    register(RaisingExtractor(), [".py"])

    # Provide a fake regex fallback that returns a CJK candidate
    class FakeFallback:
        def supports(self, path: Path) -> bool:
            return True

        def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
            return [
                Candidate(
                    segment=_make_segment(
                        path.as_posix(), "测试内容"
                    ),
                    translatable=True,
                    reason="",
                ),
            ]

    monkeypatch.setattr(
        "repo_translator.extractors.base._get_fallback_extractor",
        lambda: FakeFallback(),
    )

    report = ExtractionReport()
    segments = list(
        extract_repo(tmp_path, source_is_cjk=True, translate_code=True, report=report)
    )

    assert len(segments) == 1
    assert "测试" in segments[0].source_text
    assert "broken.py" in report.fallback_files
