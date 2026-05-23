"""FLIR HUD OCR — extract burned-in HUD overlay fields from FLIR video frames.

The DoW/DOD UAP releases are mostly footage from military FLIR / ATFLIR / Litening
targeting pods. Each frame carries burned-in HUD text: mode (BLK/WHT/IR/CAM),
zoom (NAR/MED/WIDE or x4.0), range (3.2 NM), bearing, elevation, classification
stamps (UNCLASSIFIED / TOP SECRET / FOUO / NOFORN), and a timecode (HH:MM:SS).

This tool samples frames across a video, runs tesseract on per-corner crops
with light preprocessing (greyscale → autocontrast → upscale), regex-parses the
OCR output for canonical HUD fields, and reports a per-frame breakdown plus
cross-frame consensus values.

Conventions per CLAUDE.md:
- All paths are relative to UAP_DATA_DIR.
- All results land in the SQLite cache via `corpus.put_cached`.
- No raw video bytes; only structured JSON.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import Config
from ..corpus import Corpus

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Regex set for canonical FLIR HUD fields.
# Tuned conservatively — false negatives are OK (caller can fall back to
# vision-model description); false positives are costly because they pollute
# the consensus aggregation downstream.
# ----------------------------------------------------------------------------

CLASSIFICATION_TOKENS = (
    "TOP SECRET", "SECRET", "CONFIDENTIAL", "UNCLASSIFIED",
    "FOUO", "NOFORN", "REL TO", "//",
)

# Mode tokens FLIR systems toggle between for the IR/visible bands.
MODE_TOKENS = ("BLK", "WHT", "IR", "TV", "CAM", "RGB", "NUC")

# Zoom tokens — narrow / medium / wide field-of-view bands.
ZOOM_FOV_TOKENS = ("NAR", "MED", "WIDE", "WID", "WFV", "MFV", "NFV")

RE_ZOOM_NUMERIC = re.compile(r"\b[xX]\s*(\d+(?:\.\d+)?)\b")
RE_RANGE_NM = re.compile(r"\b(\d+(?:\.\d+)?)\s*N\.?M\.?\b", re.IGNORECASE)
RE_RANGE_RNG = re.compile(r"\bRNG\s*[:=]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
RE_BEARING = re.compile(r"\b(?:BRG|BEAR)\s*[:=]?\s*(\d{1,3}(?:\.\d+)?)", re.IGNORECASE)
RE_ELEVATION = re.compile(r"\b(?:EL|ELEV)\s*[:=]?\s*([+-]?\d{1,3}(?:\.\d+)?)", re.IGNORECASE)
RE_TIMECODE = re.compile(r"\b(\d{1,2}:[0-5]\d:[0-5]\d)\b")
RE_HEADING_3DIG = re.compile(r"\b(\d{3})\b")  # bare 3-digit token (often heading)


# Corner crop fractions. FLIR HUDs cluster overlays in the four corners + the
# top/bottom strips; running OCR on each region separately gives tesseract a
# cleaner page-segmentation hint than full-frame.
HUD_REGIONS: dict[str, tuple[float, float, float, float]] = {
    # (left, upper, right, lower) as fractions of (W, H)
    "top":          (0.00, 0.00, 1.00, 0.18),
    "bottom":       (0.00, 0.82, 1.00, 1.00),
    "top_left":     (0.00, 0.00, 0.35, 0.20),
    "top_right":    (0.65, 0.00, 1.00, 0.20),
    "bottom_left":  (0.00, 0.78, 0.35, 1.00),
    "bottom_right": (0.65, 0.78, 1.00, 1.00),
    "full":         (0.00, 0.00, 1.00, 1.00),
}


def _preprocess(img):  # type: ignore[no-untyped-def]
    """Greyscale → autocontrast → 2× upscale. Light hand — heavy thresholding
    blew out HUD text on darker FLIR backgrounds in spot-testing."""
    from PIL import Image, ImageOps

    g = img.convert("L")
    g = ImageOps.autocontrast(g, cutoff=2)
    w, h = g.size
    return g.resize((w * 2, h * 2), Image.LANCZOS)


def _ocr_region(img, region: tuple[float, float, float, float]) -> str:  # type: ignore[no-untyped-def]
    import pytesseract  # local import: parse-only callers (tests) don't need tesseract installed

    w, h = img.size
    l, u, r, lo = region
    box = (int(l * w), int(u * h), int(r * w), int(lo * h))
    crop = img.crop(box)
    pp = _preprocess(crop)
    # PSM 11 = sparse text. OEM 1 = LSTM-only. config='-l eng' implicit.
    try:
        return pytesseract.image_to_string(pp, config="--psm 11 --oem 1")
    except pytesseract.TesseractNotFoundError:
        log.warning("tesseract not installed; flir_hud_ocr will return empty results")
        return ""


def _parse_fields(text: str) -> dict[str, Any]:
    """Extract canonical FLIR HUD fields from raw OCR output."""
    fields: dict[str, Any] = {}
    upper = text.upper()

    # Classification — match longest first so "TOP SECRET" doesn't shadow as "SECRET".
    for tok in CLASSIFICATION_TOKENS:
        if tok in upper:
            fields["classification"] = tok
            break

    # Mode — first hit wins (BLK and WHT can both appear in legends; the burned-in
    # current mode usually appears in the top-right corner).
    for tok in MODE_TOKENS:
        if re.search(rf"\b{tok}\b", upper):
            fields["mode"] = tok
            break

    # Zoom — try numeric first (x4.0, x10), then field-of-view token.
    m = RE_ZOOM_NUMERIC.search(text)
    if m:
        fields["zoom"] = f"x{m.group(1)}"
    else:
        for tok in ZOOM_FOV_TOKENS:
            if re.search(rf"\b{tok}\b", upper):
                fields["zoom"] = tok
                break

    # Range — prefer NM-suffixed reading, fall back to RNG label.
    m = RE_RANGE_NM.search(text)
    if m:
        try:
            fields["range_nm"] = float(m.group(1))
        except ValueError:
            pass
    if "range_nm" not in fields:
        m = RE_RANGE_RNG.search(text)
        if m:
            try:
                fields["range_nm"] = float(m.group(1))
            except ValueError:
                pass

    # Bearing
    m = RE_BEARING.search(text)
    if m:
        try:
            val = float(m.group(1))
            if 0.0 <= val <= 360.0:
                fields["bearing_deg"] = val
        except ValueError:
            pass

    # Elevation
    m = RE_ELEVATION.search(text)
    if m:
        try:
            val = float(m.group(1))
            if -90.0 <= val <= 90.0:
                fields["elevation_deg"] = val
        except ValueError:
            pass

    # Timecode
    m = RE_TIMECODE.search(text)
    if m:
        fields["timecode"] = m.group(1)

    return fields


def _consensus(per_frame: list[dict[str, Any]]) -> dict[str, Any]:
    """For each field present in any frame, pick the mode (or mean for numerics)."""
    out: dict[str, Any] = {}
    if not per_frame:
        return out

    # Categorical fields: pick the mode value with the count it was seen.
    for key in ("classification", "mode", "zoom"):
        values = [f["fields"].get(key) for f in per_frame if f["fields"].get(key)]
        if values:
            most, count = Counter(values).most_common(1)[0]
            out[key] = {"value": most, "frames_with_value": count, "frames_total": len(per_frame)}

    # Numeric fields: if all observations are within ±10% of the mean, report
    # the mean; otherwise report `unstable` with the spread.
    for key in ("range_nm", "bearing_deg", "elevation_deg"):
        values = [f["fields"].get(key) for f in per_frame if f["fields"].get(key) is not None]
        if not values:
            continue
        mean = sum(values) / len(values)
        spread = max(values) - min(values)
        # Bearing is circular — skip the stable check for it for now.
        stable = key == "bearing_deg" or (mean == 0 or spread / max(abs(mean), 1e-6) < 0.10)
        out[key] = {
            "mean": round(mean, 2),
            "min": min(values),
            "max": max(values),
            "frames_with_value": len(values),
            "stable": stable,
        }

    return out


# ----------------------------------------------------------------------------
# Public entrypoint
# ----------------------------------------------------------------------------


async def flir_hud_ocr(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    at_seconds: float | None = None,
    sample_count: int = 5,
    width: int = 1280,
    regions: list[str] | None = None,
) -> dict[str, Any]:
    """OCR FLIR HUD overlay fields from a video.

    Two modes:
      - single frame: pass `at_seconds=T` to OCR just that frame.
      - sampled: omit `at_seconds`; the tool samples `sample_count` frames evenly
        across the video and aggregates a consensus.

    Args:
        rel_path: Video path relative to UAP_DATA_DIR.
        at_seconds: If set, OCR a single frame at that timestamp.
        sample_count: How many frames to sample when at_seconds is None. Default 5.
        width: Frame width in pixels for OCR. Bigger = slower but better OCR.
        regions: Which HUD regions to OCR. Defaults to all corners + top/bottom strips.
    """
    use_regions = regions or ["top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"]
    unknown = [r for r in use_regions if r not in HUD_REGIONS]
    if unknown:
        raise ValueError(f"unknown HUD region(s): {unknown}. valid: {sorted(HUD_REGIONS)}")

    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    region_key = ",".join(sorted(use_regions))
    if at_seconds is not None:
        cache_key = f"v1|t{at_seconds:.2f}|w{width}|{region_key}"
    else:
        cache_key = f"v1|n{sample_count}|w{width}|{region_key}"
    cached = corpus.get_cached(rel_path, "flir_hud_ocr", "default", cache_key)
    if cached:
        return cached

    # Lazy imports — keep parse-only callers (unit tests) tesseract-free.
    from PIL import Image

    from .video import extract_frame, sample_frames

    if at_seconds is not None:
        frame = await extract_frame(
            cfg, corpus, rel_path,
            at_seconds=at_seconds, width=width, return_base64=False,
        )
        frames = [frame]
    else:
        frames = await sample_frames(
            cfg, corpus, rel_path, count=sample_count, width=width,
        )

    per_frame: list[dict[str, Any]] = []
    for f in frames:
        abs_frame = cfg.cache_dir / f["frame_path"]
        if not abs_frame.is_file():
            log.warning("frame missing on disk: %s", abs_frame)
            continue

        img = Image.open(abs_frame)
        region_texts: dict[str, str] = {}
        for r in use_regions:
            txt = _ocr_region(img, HUD_REGIONS[r])
            txt = txt.strip()
            if txt:
                region_texts[r] = txt

        joined = "\n".join(region_texts.values())
        fields = _parse_fields(joined)

        per_frame.append({
            "at_seconds": f["at_seconds"],
            "at_percent": f.get("at_percent"),
            "frame_path": f["frame_path"],
            "raw_text": joined,
            "region_texts": region_texts,
            "fields": fields,
            "field_count": len(fields),
        })

    result: dict[str, Any] = {
        "path": rel_path,
        "frame_count": len(per_frame),
        "frames": per_frame,
        "consensus": _consensus(per_frame),
        "fields_observed": sorted({
            k for f in per_frame for k in f["fields"]
        }),
    }
    corpus.put_cached(rel_path, "flir_hud_ocr", "default", cache_key, result)
    return result


def have_tesseract() -> bool:
    """Probe for the tesseract binary. Used by smoke tests + healthz."""
    import shutil
    return shutil.which("tesseract") is not None
