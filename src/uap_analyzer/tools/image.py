"""Image analysis tools — vision-model description."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from ..config import Config
from ..corpus import Corpus
from .ollama_client import OllamaClient

log = logging.getLogger(__name__)


# Default prompt tuned for FLIR / IR / DoD UAP imagery. The model hallucinates
# heavily if it tries to use full-color reasoning — anchor it on grayscale +
# common surfaces + redaction artifacts.
FLIR_DESCRIBE_PROMPT = """\
This is a single frame from a US military FLIR (forward-looking infrared) \
targeting pod, or a related military/government surveillance image. The image \
is typically GRAYSCALE thermal imagery, not a normal color photo. Many releases \
are over open water (ocean) or sky, not land. Solid black rectangles in the \
image are deliberate DoD REDACTIONS of HUD metadata (date, coordinates, \
classification stamps) — they are NOT real objects.

Describe ONLY what you can confirm visually, in 3-5 concise sentences:

1. What dominant surface is visible: ocean, sky, clouds, land, ground installation, building?
2. Any object visible — its rough position (e.g. "center of frame", "upper-left"), apparent size, and whether it appears to be moving (motion blur).
3. Any HUD overlay elements: crosshair, target box, range/bearing indicator, redaction rectangles, classification banner.
4. Any imaging anomalies: bloom/saturation, motion blur, diffraction spikes from a point source, parallactic motion.

If something is unclear or you can't confirm it, say so explicitly. Do NOT speculate \
about origin or what kind of craft/object it might be — just describe the visual evidence.\
"""


GENERIC_DESCRIBE_PROMPT = """\
Describe this image in 3-5 concise sentences. Focus on:
1. What is the dominant subject?
2. What text, markings, or labels are visible (transcribe exactly if legible)?
3. What is the apparent setting, time period, or context?
4. Any notable details, anomalies, or features.

If anything is unclear, say so. Do not speculate beyond what is visible.\
"""


def _pick_prompt(image_path: Path, override: str | None) -> tuple[str, str]:
    """Pick a default prompt based on path. Returns (prompt, prompt_kind)."""
    if override:
        return override, "custom"
    # Frames extracted from FLIR videos live under cache_dir/frames/ — use FLIR prompt.
    if "frames/" in str(image_path):
        return FLIR_DESCRIBE_PROMPT, "flir"
    # Photos under the corpus root — generic prompt by default. (Caller can pass
    # `prompt=FLIR_DESCRIBE_PROMPT` explicitly if they know it's FLIR/IR.)
    return GENERIC_DESCRIBE_PROMPT, "generic"


async def describe_image(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    prompt: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Vision-describe an image. Accepts a path either under data_dir
    (a corpus photo) OR under cache_dir (an extracted frame).
    """
    abs_path = _resolve_image_path(cfg, rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    used_prompt, prompt_kind = _pick_prompt(abs_path, prompt)

    # Cache key: prompt content + model + the image's content hash.
    img_hash = hashlib.sha256(abs_path.read_bytes()).hexdigest()[:16]
    prompt_hash = hashlib.sha256(used_prompt.encode()).hexdigest()[:12]
    model_id = model or cfg.ollama_vision_model
    params_hash = f"v1|{img_hash}|{prompt_hash}|{model_id}"

    # Cache by the path the user supplied (so list_corpus identifies it).
    cached = corpus.get_cached(rel_path, "describe_image", "vision", params_hash)
    if cached:
        return cached

    client = OllamaClient(cfg)
    try:
        res = await client.describe_image(abs_path, used_prompt, model=model_id)
    finally:
        await client.aclose()

    result = {
        "path": rel_path,
        "description": res["content"].strip(),
        "model": res["model"],
        "prompt_kind": prompt_kind,
        "tokens": res["eval_count"],
        "duration_s": round(res["total_duration_s"], 2),
    }
    corpus.put_cached(rel_path, "describe_image", "vision", params_hash, result)
    return result


def _resolve_image_path(cfg: Config, rel_path: str) -> Path:
    """Resolve a path that may live under data_dir or cache_dir.

    For absolute paths, pick the matching root. For relative paths, try
    cache_dir first (extracted frames most commonly), then data_dir.
    """
    p = Path(rel_path)
    if p.is_absolute():
        p = p.resolve()
        for root in (cfg.cache_dir, cfg.data_dir):
            try:
                p.relative_to(root)
                if p.is_file():
                    return p
            except ValueError:
                continue
        raise ValueError(
            f"path {rel_path!r} resolves outside data_dir or cache_dir, "
            f"or does not exist"
        )

    # Relative: try cache_dir then data_dir, returning the first that exists.
    for root in (cfg.cache_dir, cfg.data_dir):
        candidate = (root / p).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(rel_path)
