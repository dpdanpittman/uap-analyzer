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

# Vision-model whitelist for the `vision_model` arg passed to flir_hud_ocr
# (mode="vision"). Sibling tools (transcribe_audio, detect_objects) gate their
# model parameter against an enum — this one didn't, which let a client
# inflate the cache and the model-cache via arbitrary names. (Tribunal sec-F-sec-002.)
VALID_HUD_MODELS = frozenset({
    "qwen2.5vl:7b",
    "qwen2.5vl:32b",
    "qwen2.5vl:72b",
    "qwen2-vl:7b",
    "llama3.2-vision:11b",
    "llama3.2-vision:90b",
    "minicpm-v:8b",
})

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
# ----------------------------------------------------------------------------
# Vision-mode prompt — strictly structured-output extraction. Used by
# flir_hud_ocr(mode="vision"). Paired with ollama's `format: "json"` decoding
# constraint, this yields parseable JSON with the same field shape as the
# OCR-mode regex parser.
# ----------------------------------------------------------------------------

FLIR_HUD_VISION_PROMPT = """You are analyzing a single frame from a US military FLIR / IR
targeting pod video (ATFLIR, Litening pod, AAQ-28, etc.) released by the
Department of War as UAP disclosure material.

Your task is HUD-text extraction only. Read any burned-in HUD overlay text
visible on the frame and return a strict JSON object with these fields.
Return `null` for any field that is not clearly legible — do not guess.

Schema:
{
  "classification": one of "TOP SECRET" | "SECRET" | "CONFIDENTIAL" | "UNCLASSIFIED" | "FOUO" | "NOFORN" | null,
  "mode": one of "BLK" | "WHT" | "IR" | "TV" | "CAM" | "RGB" | "NUC" | null,
  "zoom": string like "x4.0" or one of "NAR" | "MED" | "WIDE" | null,
  "range_nm": number (nautical miles, e.g. 3.2) or null,
  "bearing_deg": integer 0..360 or null,
  "elevation_deg": integer -90..90 or null,
  "timecode": string "HH:MM:SS" or null,
  "raw_text": short string with the verbatim HUD text you read (for audit), or empty string
}

Rules:
- If you cannot read a field with high confidence, return null for it.
- Do not infer fields from context — only report what is visibly burned into the HUD overlay.
- Return ONLY the JSON object. No prose, no explanation, no markdown fences.
"""

# Fields the vision response is allowed to return — used for cross-mode
# normalization so OCR mode and vision mode produce comparable outputs.
VISION_FIELDS = ("classification", "mode", "zoom", "range_nm", "bearing_deg", "elevation_deg", "timecode")


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


def _normalize_vision_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + coerce the vision model's JSON output into the same field
    shape as the OCR-mode regex parser. Drops anything null, out-of-range, or
    not in the allowed enum set so consensus aggregation stays consistent
    across modes."""
    out: dict[str, Any] = {}

    cls = raw.get("classification")
    if isinstance(cls, str) and cls.upper() in {t.upper() for t in CLASSIFICATION_TOKENS}:
        out["classification"] = cls.upper()

    mode = raw.get("mode")
    if isinstance(mode, str) and mode.upper() in {t.upper() for t in MODE_TOKENS}:
        out["mode"] = mode.upper()

    zoom = raw.get("zoom")
    if isinstance(zoom, str) and zoom.strip():
        z = zoom.strip()
        # Accept either "x4.0" form or a FOV token.
        if RE_ZOOM_NUMERIC.search(z) or z.upper() in {t.upper() for t in ZOOM_FOV_TOKENS}:
            out["zoom"] = z if z.startswith("x") or z.startswith("X") else z.upper()

    # Note on isinstance(..., (int, float)) below: bool is a subclass of int in
    # Python, so `True`/`False` would otherwise sneak through as 1.0/0.0.
    # Reject explicit bool first. (Tribunal sec-F-sec-010.)
    rng = raw.get("range_nm")
    if isinstance(rng, (int, float)) and not isinstance(rng, bool) and 0 < rng < 1000:
        out["range_nm"] = float(rng)

    brg = raw.get("bearing_deg")
    if isinstance(brg, (int, float)) and not isinstance(brg, bool) and 0 <= brg <= 360:
        out["bearing_deg"] = float(brg)

    el = raw.get("elevation_deg")
    if isinstance(el, (int, float)) and not isinstance(el, bool) and -90 <= el <= 90:
        out["elevation_deg"] = float(el)

    tc = raw.get("timecode")
    if isinstance(tc, str) and RE_TIMECODE.match(tc.strip()):
        out["timecode"] = tc.strip()

    return out


# Maximum size of the model's JSON response we'll attempt to parse. Caps both
# the JSON-decoder work and the recursion depth a hostile model output could
# trigger via deeply-nested arrays. (Tribunal sec-F-sec-006.)
_VISION_JSON_MAX_BYTES = 64 * 1024


async def _vision_extract_frame(
    client: Any, frame_path: Path, *, model: str
) -> dict[str, Any]:
    """Send a frame to the vision model with the structured-HUD prompt; parse
    JSON back; return (normalized_fields_dict, raw_model_text, model_used).

    Takes a pre-built `OllamaClient` — caller is responsible for lifecycle.
    Hoisting the client out of the per-frame loop preserves TCP keepalive
    across frames and avoids the per-call handshake cost. (Tribunal arch-F-arch-005,
    perf-F-perf-004.)
    """
    resp = await client.describe_image(
        frame_path,
        FLIR_HUD_VISION_PROMPT,
        temperature=0.05,
        max_tokens=512,
        model=model,
        json_mode=True,
    )

    content = (resp.get("content") or "").strip()
    parsed: dict[str, Any] = {}
    raw_text = ""
    if len(content) > _VISION_JSON_MAX_BYTES:
        log.warning(
            "vision-mode response %d bytes exceeds parse cap %d; clamping",
            len(content), _VISION_JSON_MAX_BYTES,
        )
        content = content[:_VISION_JSON_MAX_BYTES]
    try:
        import json as _json
        # ollama JSON mode is usually clean; defensive parse in case the model
        # wrapped the JSON in markdown fences anyway.
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json\n"):
                content = content[5:]
        obj = _json.loads(content) if content else {}
        if isinstance(obj, dict):
            parsed = obj
            raw_text = str(obj.get("raw_text", ""))[:240]
    except (ValueError, TypeError, RecursionError) as e:
        log.warning("vision-mode JSON parse failed: %s; content=%s", e, content[:200])

    fields = _normalize_vision_fields(parsed)
    return {
        "fields": fields,
        "raw_text": raw_text,
        "model": resp.get("model") or model,
        "duration_s": resp.get("total_duration_s"),
    }


async def flir_hud_ocr(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    mode: str = "ocr",
    at_seconds: float | None = None,
    sample_count: int = 5,
    width: int = 1280,
    regions: list[str] | None = None,
    vision_model: str | None = None,
) -> dict[str, Any]:
    """Extract FLIR HUD overlay fields from a video.

    Two extraction modes:
      - "ocr" (default): tesseract over per-corner crops. Fast and cheap. Best for
        clear, high-contrast HUD overlays. Returns lots of raw_text per region.
      - "vision": qwen2.5vl (or any ollama vision model) with a structured-JSON
        prompt. Slower but far more accurate on FLIR HUDs where tesseract
        struggles (anti-aliased fonts, low-contrast IR backgrounds).

    Two sampling modes:
      - single frame: pass `at_seconds=T` to extract just that timestamp.
      - sampled (default): samples `sample_count` frames evenly across the video.

    Args:
        rel_path: Video path relative to UAP_DATA_DIR.
        mode: "ocr" (tesseract) | "vision" (qwen2.5vl).
        at_seconds: If set, extract a single frame at this timestamp.
        sample_count: Frames to sample when at_seconds is None. Default 5.
        width: Frame width in pixels. Bigger = slower but better extraction.
        regions: HUD regions for ocr mode. Ignored in vision mode (model sees
                 the whole frame). Defaults to all corners + top/bottom strips.
        vision_model: Override OLLAMA_HUD_MODEL for vision mode (e.g. switch
                      to llama3.2-vision:11b for comparison).
    """
    if mode not in ("ocr", "vision"):
        raise ValueError(f"unknown mode {mode!r}; valid: 'ocr', 'vision'")

    use_regions = regions or ["top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"]
    if mode == "ocr":
        unknown = [r for r in use_regions if r not in HUD_REGIONS]
        if unknown:
            raise ValueError(f"unknown HUD region(s): {unknown}. valid: {sorted(HUD_REGIONS)}")

    # Whitelist `vision_model`. The sibling tools (transcribe_audio,
    # detect_objects) gate their model param against a tuple — this one
    # didn't, which let arbitrary client-supplied names inflate the cache
    # key namespace + the ollama model cache. (Tribunal sec-F-sec-002.)
    resolved_vision_model: str | None = None
    if mode == "vision":
        resolved_vision_model = vision_model or cfg.ollama_hud_model
        if resolved_vision_model not in VALID_HUD_MODELS:
            raise ValueError(
                f"unknown vision_model {resolved_vision_model!r}; "
                f"valid: {sorted(VALID_HUD_MODELS)}"
            )

    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    # Cache key folds in every output-affecting param via hash (sec-F-sec-005).
    region_key = ",".join(sorted(use_regions))
    model_key = resolved_vision_model or "n/a"
    if at_seconds is not None:
        key_h = hashlib.sha256(
            "|".join(("v3", mode, f"t{at_seconds:.2f}", str(width), region_key, model_key)).encode()
        ).hexdigest()[:16]
        cache_key = f"v3|m{mode}|t{at_seconds:.2f}|{key_h}"
    else:
        key_h = hashlib.sha256(
            "|".join(("v3", mode, f"n{sample_count}", str(width), region_key, model_key)).encode()
        ).hexdigest()[:16]
        cache_key = f"v3|m{mode}|n{sample_count}|{key_h}"
    cached = corpus.get_cached(rel_path, "flir_hud_ocr", mode, cache_key)
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

    # Build one OllamaClient for the whole vision-mode sweep so TCP keepalive
    # is preserved across frames. (Tribunal perf-F-perf-004, arch-F-arch-005.)
    vision_client: Any = None
    if mode == "vision":
        from .ollama_client import OllamaClient
        vision_client = OllamaClient(cfg)

    per_frame: list[dict[str, Any]] = []
    try:
        for f in frames:
            abs_frame = cfg.cache_dir / f["frame_path"]
            if not abs_frame.is_file():
                log.warning("frame missing on disk: %s", abs_frame)
                continue

            if mode == "ocr":
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
                    "extraction_mode": "ocr",
                    "raw_text": joined,
                    "region_texts": region_texts,
                    "fields": fields,
                    "field_count": len(fields),
                })
            else:
                # vision mode
                try:
                    v = await _vision_extract_frame(
                        vision_client, abs_frame, model=resolved_vision_model,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("vision extract failed at t=%s: %r", f.get("at_seconds"), e)
                    v = {"fields": {}, "raw_text": "", "model": None, "duration_s": None,
                         "error": str(e)[:240]}

                per_frame.append({
                    "at_seconds": f["at_seconds"],
                    "at_percent": f.get("at_percent"),
                    "frame_path": f["frame_path"],
                    "extraction_mode": "vision",
                    "raw_text": v.get("raw_text", ""),
                    "fields": v["fields"],
                    "field_count": len(v["fields"]),
                    "model": v.get("model"),
                    "duration_s": v.get("duration_s"),
                    **({"error": v["error"]} if "error" in v else {}),
                })
    finally:
        if vision_client is not None:
            await vision_client.aclose()

    result: dict[str, Any] = {
        "path": rel_path,
        "mode": mode,
        "frame_count": len(per_frame),
        "frames": per_frame,
        "consensus": _consensus(per_frame),
        "fields_observed": sorted({
            k for f in per_frame for k in f["fields"]
        }),
    }
    if mode == "vision":
        result["vision_model"] = resolved_vision_model

    corpus.put_cached(rel_path, "flir_hud_ocr", mode, cache_key, result)
    return result


def have_tesseract() -> bool:
    """Probe for the tesseract binary. Used by smoke tests + healthz."""
    import shutil
    return shutil.which("tesseract") is not None
