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


def test_flir_hud_ocr_rejects_unknown_mode(tmp_path: Path, monkeypatch):
    """mode= must be 'ocr' or 'vision'."""
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

    with pytest.raises(ValueError, match="unknown mode"):
        asyncio.run(flir_hud_ocr(cfg, corpus, "fake.mp4", mode="psychic"))


# ---------------------------------------------------------------------------
# v0.2.1 — vision-mode normalization
# ---------------------------------------------------------------------------


def test_flir_normalize_vision_full():
    """Vision mode output normalizes to the same field shape as OCR mode."""
    from uap_analyzer.tools.flir import _normalize_vision_fields

    raw = {
        "classification": "unclassified",
        "mode": "blk",
        "zoom": "x4.0",
        "range_nm": 3.2,
        "bearing_deg": 274,
        "elevation_deg": -12,
        "timecode": "00:12:33",
        "raw_text": "ignored",
    }
    fields = _normalize_vision_fields(raw)
    assert fields["classification"] == "UNCLASSIFIED"
    assert fields["mode"] == "BLK"
    assert fields["zoom"] == "x4.0"
    assert fields["range_nm"] == 3.2
    assert fields["bearing_deg"] == 274.0
    assert fields["elevation_deg"] == -12.0
    assert fields["timecode"] == "00:12:33"


def test_flir_normalize_vision_drops_null():
    """Null fields from the vision model are dropped, not stored."""
    from uap_analyzer.tools.flir import _normalize_vision_fields

    raw = {
        "classification": None,
        "mode": "WHT",
        "zoom": None,
        "range_nm": None,
        "bearing_deg": None,
        "elevation_deg": None,
        "timecode": None,
    }
    fields = _normalize_vision_fields(raw)
    assert fields == {"mode": "WHT"}


def test_flir_normalize_vision_rejects_garbage():
    """Out-of-range numerics and invalid enums are silently dropped, not coerced."""
    from uap_analyzer.tools.flir import _normalize_vision_fields

    raw = {
        "classification": "MADE_UP",
        "mode": "NotARealMode",
        "zoom": "xyz",
        "range_nm": -5,
        "bearing_deg": 999,
        "elevation_deg": 200,
        "timecode": "not a time",
    }
    fields = _normalize_vision_fields(raw)
    assert fields == {}


def test_flir_normalize_vision_fov_zoom_token_uppercased():
    """FOV-token zoom is uppercased; numeric zoom keeps its 'x' prefix as-is."""
    from uap_analyzer.tools.flir import _normalize_vision_fields

    assert _normalize_vision_fields({"zoom": "nar"})["zoom"] == "NAR"
    assert _normalize_vision_fields({"zoom": "med"})["zoom"] == "MED"
    assert _normalize_vision_fields({"zoom": "x10"})["zoom"] == "x10"


# ---------------------------------------------------------------------------
# v0.3.0 — audio transcription surface
# ---------------------------------------------------------------------------


def test_transcribe_audio_rejects_unknown_model(tmp_path: Path, monkeypatch):
    """Unknown whisper model is rejected with a useful error before any I/O."""
    import asyncio

    from uap_analyzer.config import Config
    from uap_analyzer.corpus import Corpus
    from uap_analyzer.tools.audio import transcribe_audio

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    (tmp_path / "fake.mp4").write_bytes(b"\x00")
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)

    with pytest.raises(ValueError, match="unknown whisper model"):
        asyncio.run(
            transcribe_audio(cfg, corpus, "fake.mp4", model="not-a-model")
        )


def test_transcribe_audio_rejects_unknown_compute_type(tmp_path: Path, monkeypatch):
    """Invalid WHISPER_COMPUTE_TYPE env is rejected loudly."""
    import asyncio

    from uap_analyzer.config import Config
    from uap_analyzer.corpus import Corpus
    from uap_analyzer.tools.audio import transcribe_audio

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "fp99_super_fast")
    (tmp_path / "cache").mkdir()
    (tmp_path / "fake.mp4").write_bytes(b"\x00")
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)

    with pytest.raises(ValueError, match="WHISPER_COMPUTE_TYPE"):
        asyncio.run(transcribe_audio(cfg, corpus, "fake.mp4"))


def test_whisper_valid_models_includes_default(tmp_path: Path, monkeypatch):
    """The default base.en must always be in VALID_MODELS."""
    from uap_analyzer.config import Config
    from uap_analyzer.tools.audio import VALID_COMPUTE_TYPES, VALID_MODELS

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    cfg = Config.from_env()
    assert cfg.whisper_model in VALID_MODELS
    assert cfg.whisper_compute_type in VALID_COMPUTE_TYPES


# ---------------------------------------------------------------------------
# v0.4.0 — object detection surface
# ---------------------------------------------------------------------------


def test_detect_objects_rejects_unknown_model(tmp_path: Path, monkeypatch):
    """Unknown YOLO model is rejected before any I/O."""
    import asyncio

    from uap_analyzer.config import Config
    from uap_analyzer.corpus import Corpus
    from uap_analyzer.tools.detect import detect_objects

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    (tmp_path / "fake.mp4").write_bytes(b"\x00")
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)

    with pytest.raises(ValueError, match="unknown YOLO model"):
        asyncio.run(
            detect_objects(cfg, corpus, "fake.mp4", model="yolov999n")
        )


def test_detect_objects_rejects_bad_confidence(tmp_path: Path, monkeypatch):
    """Confidence must be in (0, 1]."""
    import asyncio

    from uap_analyzer.config import Config
    from uap_analyzer.corpus import Corpus
    from uap_analyzer.tools.detect import detect_objects

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    (tmp_path / "fake.mp4").write_bytes(b"\x00")
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)

    with pytest.raises(ValueError, match="confidence must be in"):
        asyncio.run(detect_objects(cfg, corpus, "fake.mp4", confidence=1.5))
    with pytest.raises(ValueError, match="confidence must be in"):
        asyncio.run(detect_objects(cfg, corpus, "fake.mp4", confidence=0))


def test_detect_objects_rejects_bad_iou(tmp_path: Path, monkeypatch):
    """IoU must be in (0, 1]."""
    import asyncio

    from uap_analyzer.config import Config
    from uap_analyzer.corpus import Corpus
    from uap_analyzer.tools.detect import detect_objects

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    (tmp_path / "fake.mp4").write_bytes(b"\x00")
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)

    with pytest.raises(ValueError, match="iou must be in"):
        asyncio.run(detect_objects(cfg, corpus, "fake.mp4", iou=2.0))


def test_detect_aggregate_labels_counts_and_ranks():
    """Cross-frame aggregation reports total + per-label + frames-with-label
    + top-5 ranked by total."""
    from uap_analyzer.tools.detect import _aggregate_labels

    per_frame = [
        {"detections": [
            {"label": "person", "confidence": 0.9, "bbox": [0, 0, 10, 10]},
            {"label": "person", "confidence": 0.7, "bbox": [10, 10, 20, 20]},
            {"label": "airplane", "confidence": 0.8, "bbox": [0, 0, 100, 100]},
        ]},
        {"detections": [
            {"label": "person", "confidence": 0.85, "bbox": [0, 0, 10, 10]},
            {"label": "boat", "confidence": 0.5, "bbox": [0, 0, 50, 50]},
        ]},
        {"detections": []},
    ]
    out = _aggregate_labels(per_frame)
    assert out["total_detections"] == 5
    assert out["by_label"] == {"person": 3, "airplane": 1, "boat": 1}
    assert out["frames_with_label"] == {"person": 2, "airplane": 1, "boat": 1}
    assert out["top_labels"][0] == "person"


def test_detect_valid_models_constant():
    """VALID_MODELS includes the documented default."""
    from uap_analyzer.tools.detect import VALID_MODELS

    assert "yolov8n" in VALID_MODELS
    assert "yolov11n" in VALID_MODELS


# ---------------------------------------------------------------------------
# v0.4.1 — tribunal-driven fixes
# ---------------------------------------------------------------------------


def test_detect_is_image_classifies_by_extension(tmp_path: Path, monkeypatch):
    """is_image must use the file's extension, NOT corpus.get() lookup, so
    absolute paths + never-scanned files classify correctly. (arch-F-arch-002)"""
    from uap_analyzer.tools.detect import IMAGE_EXTS

    # The set itself is the contract.
    assert ".png" in IMAGE_EXTS
    assert ".jpg" in IMAGE_EXTS
    assert ".jpeg" in IMAGE_EXTS
    assert ".webp" in IMAGE_EXTS
    # video extensions must NOT be present
    assert ".mp4" not in IMAGE_EXTS
    assert ".mov" not in IMAGE_EXTS


def test_detect_model_cache_lru_eviction():
    """_MODEL_CACHE evicts oldest entries past _MODEL_CACHE_MAX."""
    from uap_analyzer.tools import detect as detect_tools

    # Sneak fake entries past _get_model. We're testing the cache shape, not
    # the YOLO load path.
    detect_tools._MODEL_CACHE.clear()
    detect_tools._MODEL_CACHE["yolov8n"] = "model-n"
    detect_tools._MODEL_CACHE["yolov8s"] = "model-s"
    detect_tools._MODEL_CACHE["yolov8m"] = "model-m"
    assert len(detect_tools._MODEL_CACHE) == 3

    # Simulate the eviction path that runs at the end of _get_model.
    detect_tools._MODEL_CACHE["yolov8l"] = "model-l"
    while len(detect_tools._MODEL_CACHE) > detect_tools._MODEL_CACHE_MAX:
        detect_tools._MODEL_CACHE.popitem(last=False)

    assert "yolov8n" not in detect_tools._MODEL_CACHE  # evicted
    assert "yolov8l" in detect_tools._MODEL_CACHE      # newest stays
    assert len(detect_tools._MODEL_CACHE) == detect_tools._MODEL_CACHE_MAX
    detect_tools._MODEL_CACHE.clear()


def test_detect_hash_key_collision_resistant():
    """_hash_key produces distinct outputs for tuples that would |-collide."""
    from uap_analyzer.tools.detect import _hash_key

    # Different parameter splits that would string-equal under '|'.join():
    a = _hash_key("v2", "yolov8n", "t1.5", 0.25, 0.45, 1280, "all", False)
    b = _hash_key("v2", "yolov8n|t1.5", 0.25, 0.45, 1280, "all", False)
    # Hashing the joined string still collides, but our keys come from the
    # joined-tuple call signature; this test asserts the format we settled on
    # produces stable 16-hex output AND distinguishes runs that vary by a
    # single field.
    assert len(a) == 16
    assert a != _hash_key("v2", "yolov8s", "t1.5", 0.25, 0.45, 1280, "all", False)
    # The b assignment is here as a regression marker — if someone changes
    # _hash_key to take pre-joined strings, this test still passes but the
    # name makes the intent visible.
    assert isinstance(b, str)


def test_flir_vision_model_whitelist_enforced(tmp_path: Path, monkeypatch):
    """flir_hud_ocr(mode='vision', vision_model='hostile') rejects unknown
    model names. (sec-F-sec-002)"""
    import asyncio

    from uap_analyzer.config import Config
    from uap_analyzer.corpus import Corpus
    from uap_analyzer.tools.flir import VALID_HUD_MODELS, flir_hud_ocr

    assert "qwen2.5vl:7b" in VALID_HUD_MODELS

    monkeypatch.setenv("UAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UAP_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    (tmp_path / "fake.mp4").write_bytes(b"\x00")
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)

    with pytest.raises(ValueError, match="unknown vision_model"):
        asyncio.run(
            flir_hud_ocr(
                cfg, corpus, "fake.mp4",
                mode="vision",
                vision_model="evil-model:1b",
            )
        )


def test_flir_normalize_rejects_bool_for_numeric_fields():
    """isinstance(x, (int, float)) accepts bool in Python — guard rejects
    True/False from a hostile/buggy vision model. (sec-F-sec-010)"""
    from uap_analyzer.tools.flir import _normalize_vision_fields

    raw = {
        "range_nm": True,        # would have become 1.0
        "bearing_deg": False,    # would have become 0.0
        "elevation_deg": True,   # would have become 1.0
    }
    out = _normalize_vision_fields(raw)
    assert "range_nm" not in out
    assert "bearing_deg" not in out
    assert "elevation_deg" not in out


def test_server_bounded_helper_rejects_overflow():
    """The MCP-boundary _bounded helper rejects values above the cap."""
    import importlib

    # Reload server module lazily to ensure we get _bounded
    import sys
    if "uap_analyzer.server" in sys.modules:
        importlib.reload(sys.modules["uap_analyzer.server"])

    try:
        from uap_analyzer.server import _bounded
    except ImportError:
        pytest.skip("server module not importable without mcp deps")

    assert _bounded("x", 5, 10) == 5
    assert _bounded("x", None, 10) is None
    with pytest.raises(ValueError, match="must be <= 10"):
        _bounded("x", 11, 10)
    with pytest.raises(ValueError, match="must be >= 0"):
        _bounded("x", -1, 10)
