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


# ---------------------------------------------------------------------------
# flir_hud_ocr (v0.2.0)
# ---------------------------------------------------------------------------


def test_flir_parse_fields_basic():
    """Synthetic OCR string parses canonical FLIR fields."""
    from uap_analyzer.tools.flir import _parse_fields

    # Composite of the kinds of strings tesseract emits from FLIR HUDs.
    txt = """UNCLASSIFIED//FOUO
BLK   x4.0
RNG 3.2 NM
BRG 274
EL -12
00:12:33"""
    fields = _parse_fields(txt)
    assert fields["classification"] == "UNCLASSIFIED"
    assert fields["mode"] == "BLK"
    assert fields["zoom"] == "x4.0"
    assert fields["range_nm"] == 3.2
    assert fields["bearing_deg"] == 274
    assert fields["elevation_deg"] == -12
    assert fields["timecode"] == "00:12:33"


def test_flir_parse_fields_zoom_fov_token():
    """Falls back to NAR/MED/WIDE when no numeric zoom is present."""
    from uap_analyzer.tools.flir import _parse_fields

    fields = _parse_fields("WHT NAR SECRET")
    assert fields["mode"] == "WHT"
    assert fields["zoom"] == "NAR"
    assert fields["classification"] == "SECRET"


def test_flir_parse_fields_rejects_out_of_range():
    """Bearing must be 0..360; elevation must be -90..90."""
    from uap_analyzer.tools.flir import _parse_fields

    fields = _parse_fields("BRG 999 EL 200")
    assert "bearing_deg" not in fields
    assert "elevation_deg" not in fields


def test_flir_consensus_categorical():
    """Categorical fields pick the mode across frames."""
    from uap_analyzer.tools.flir import _consensus

    per_frame = [
        {"fields": {"mode": "BLK", "classification": "UNCLASSIFIED"}},
        {"fields": {"mode": "BLK", "classification": "UNCLASSIFIED"}},
        {"fields": {"mode": "WHT", "classification": "UNCLASSIFIED"}},
    ]
    out = _consensus(per_frame)
    assert out["mode"]["value"] == "BLK"
    assert out["mode"]["frames_with_value"] == 2
    assert out["mode"]["frames_total"] == 3
    assert out["classification"]["value"] == "UNCLASSIFIED"


def test_flir_consensus_numeric_stable():
    """Numeric consensus reports mean + stable=true when spread is tight."""
    from uap_analyzer.tools.flir import _consensus

    per_frame = [
        {"fields": {"range_nm": 3.1}},
        {"fields": {"range_nm": 3.2}},
        {"fields": {"range_nm": 3.15}},
    ]
    out = _consensus(per_frame)
    assert out["range_nm"]["stable"] is True
    assert 3.0 < out["range_nm"]["mean"] < 3.3


def test_flir_consensus_numeric_unstable():
    """Wide spread flips stable=false."""
    from uap_analyzer.tools.flir import _consensus

    per_frame = [
        {"fields": {"range_nm": 1.0}},
        {"fields": {"range_nm": 8.0}},
    ]
    out = _consensus(per_frame)
    assert out["range_nm"]["stable"] is False


def test_flir_hud_ocr_rejects_unknown_region(tmp_path: Path, monkeypatch):
    """Unknown region keys are rejected with a useful error."""
    import asyncio

    from uap_analyzer.config import Config
    from uap_analyzer.corpus import Corpus
    from uap_analyzer.tools.flir import flir_hud_ocr

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    (tmp_path / "fake.mp4").write_bytes(b"\x00")
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)

    with pytest.raises(ValueError, match="unknown HUD region"):
        asyncio.run(
            flir_hud_ocr(cfg, corpus, "fake.mp4", regions=["nowhere"])
        )
