"""Smoke tests — just make sure the server imports + can scan a directory."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


def test_imports():
    """Server module imports cleanly."""
    from uap_analyzer import server  # noqa: F401
    from uap_analyzer.config import Config  # noqa: F401
    from uap_analyzer.corpus import Corpus  # noqa: F401


def test_corpus_scan_empty_dir(tmp_path: Path):
    """Empty dir scans without errors."""
    from uap_analyzer.corpus import Corpus

    data = tmp_path / "data"
    cache = tmp_path / "cache"
    data.mkdir()
    cache.mkdir()
    c = Corpus(data, cache)
    summary = c.scan()
    assert summary["seen"] == 0
    assert c.list() == []


def test_corpus_classify(tmp_path: Path):
    """Files get classified by extension."""
    from uap_analyzer.corpus import Corpus

    data = tmp_path / "data"
    cache = tmp_path / "cache"
    data.mkdir()
    cache.mkdir()
    (data / "test.mp4").write_bytes(b"\x00" * 1024)
    (data / "test.pdf").write_bytes(b"%PDF-1.4\n" + b"\x00" * 1024)
    (data / "test.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 1024)
    c = Corpus(data, cache)
    c.scan()
    items = c.list()
    kinds = sorted(item["kind"] for item in items)
    assert kinds == ["image", "pdf", "video"]


def test_config_path_resolution(tmp_path: Path, monkeypatch):
    """resolve_corpus_path rejects paths outside data_dir."""
    from uap_analyzer.config import Config

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    cfg = Config.from_env()

    (tmp_path / "a.mp4").write_bytes(b"")
    assert cfg.resolve_corpus_path("a.mp4") == (tmp_path / "a.mp4").resolve()
    assert cfg.resolve_corpus_path(str(tmp_path / "a.mp4")) == (tmp_path / "a.mp4").resolve()

    with pytest.raises(ValueError):
        cfg.resolve_corpus_path("../etc/passwd")
    with pytest.raises(ValueError):
        cfg.resolve_corpus_path("/etc/passwd")
