"""Object detection via YOLOv8/v11 (ultralytics, CPU inference).

What this is for:
  Sample N frames across a video (or take a single frame), run YOLO over
  each, and return per-timestamp [label, confidence, bbox] lists. Pairs
  naturally with flir_hud_ocr (HUD context) and analyze_video(mode="describe")
  (semantic narration) to give three independent reads on the same footage.

What this is NOT good for, before someone gets excited:
  COCO-pretrained YOLO knows 80 ordinary-world classes (person, airplane,
  car, boat, traffic light, …). FLIR / IR-mode footage on the UAP corpus
  shows unlabeled thermal blobs that COCO has no category for — expect
  zero confident detections on those clips. The signal will come from
  daylight / TV-mode footage and from photo-format material.

Conventions per CLAUDE.md:
  - All paths are relative to UAP_DATA_DIR.
  - All results land in the SQLite cache via `corpus.put_cached`.
  - No raw image bytes returned; only labels + confidences + bboxes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import Config
from ..corpus import Corpus

log = logging.getLogger(__name__)


# Module-level model cache — ultralytics' YOLO() is expensive to instantiate
# (loads ~6MB of weights + builds the inference graph). Keep one per (variant)
# alive for the process lifetime so repeated calls are cheap.
_MODEL_CACHE: dict[str, Any] = {}


# Friendly aliases → ultralytics weight filenames. We default to yolov8n
# (nano, 6.2MB) for fast CPU inference; users can opt up via the `model` arg.
VALID_MODELS = (
    "yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x",
    "yolov11n", "yolov11s", "yolov11m", "yolov11l", "yolov11x",
)


def _model_filename(name: str) -> str:
    """Map shortname (yolov8n) to ultralytics weight filename (yolov8n.pt)."""
    if name not in VALID_MODELS:
        raise ValueError(
            f"unknown YOLO model {name!r}; valid: {VALID_MODELS}"
        )
    return f"{name}.pt"


def _get_model(cfg: Config, name: str):
    """Get-or-create a YOLO model instance. The first call downloads weights
    (~6MB for yolov8n) to YOLO_CONFIG_DIR. Subsequent calls return the cached
    instance — meaningfully faster for repeated detections in one process."""
    if name in _MODEL_CACHE:
        return _MODEL_CACHE[name]

    from ultralytics import YOLO
    from ultralytics import settings as ul_settings

    # Disable ultralytics' anonymized usage telemetry — same posture as
    # HF_HUB_DISABLE_TELEMETRY for whisper. Belt-and-suspenders.
    try:
        ul_settings.update({"sync": False})
    except Exception:  # noqa: BLE001
        pass

    weight_dir = Path(os.environ.get("YOLO_CONFIG_DIR", str(cfg.cache_dir / "yolo")))
    weight_dir.mkdir(parents=True, exist_ok=True)
    weight_path = weight_dir / _model_filename(name)

    # ultralytics will download to the current working directory if the file
    # doesn't exist at the given path. Cd into the weight dir for the load so
    # the download lands in the right place. Restore cwd immediately after.
    old_cwd = Path.cwd()
    try:
        os.chdir(weight_dir)
        log.info("loading YOLO model %s — first call may download weights", name)
        model = YOLO(_model_filename(name))
    finally:
        os.chdir(old_cwd)

    _MODEL_CACHE[name] = model
    return model


def _aggregate_labels(per_frame: list[dict[str, Any]]) -> dict[str, Any]:
    """For each label observed across frames, count how often it appeared
    (across all detections, not just per-frame distinct hits) and return a
    Counter-style dict + the top-5 most-seen labels."""
    counter: Counter[str] = Counter()
    frames_with_label: Counter[str] = Counter()
    for f in per_frame:
        labels_this_frame = set()
        for d in f["detections"]:
            counter[d["label"]] += 1
            labels_this_frame.add(d["label"])
        for label in labels_this_frame:
            frames_with_label[label] += 1
    return {
        "total_detections": sum(counter.values()),
        "by_label": dict(counter),
        "frames_with_label": dict(frames_with_label),
        "top_labels": [label for label, _ in counter.most_common(5)],
    }


async def detect_objects(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    at_seconds: float | None = None,
    sample_count: int = 5,
    confidence: float = 0.25,
    iou: float = 0.45,
    classes: list[str] | None = None,
    model: str = "yolov8n",
    width: int = 1280,
) -> dict[str, Any]:
    """Run YOLO over sampled frames of a video (or one frame, or an image).

    Args:
        rel_path: Video or image path relative to UAP_DATA_DIR.
        at_seconds: If set, run detection on a single frame at this timestamp.
        sample_count: Frames to sample when at_seconds is None. Default 5.
        confidence: Min confidence to keep a detection. Default 0.25.
        iou: Non-max-suppression IoU threshold. Default 0.45.
        classes: Optional COCO label names to keep (e.g. ["airplane", "person"]).
                 None = keep all 80 classes.
        model: YOLO variant. yolov8n (default, fast/small) → yolov8x (slow/big).
               yolov11* also accepted.
        width: Frame width for inference. 1280 is YOLO's native; bumping it
               wastes time for marginal gain.
    """
    if not 0.0 < confidence <= 1.0:
        raise ValueError(f"confidence must be in (0, 1]; got {confidence}")
    if not 0.0 < iou <= 1.0:
        raise ValueError(f"iou must be in (0, 1]; got {iou}")
    if model not in VALID_MODELS:
        raise ValueError(f"unknown YOLO model {model!r}; valid: {VALID_MODELS}")

    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    # Build a stable cache key.
    class_key = ",".join(sorted(classes)) if classes else "all"
    if at_seconds is not None:
        cache_key = f"v1|m{model}|t{at_seconds:.2f}|c{confidence}|i{iou}|w{width}|{class_key}"
    else:
        cache_key = f"v1|m{model}|n{sample_count}|c{confidence}|i{iou}|w{width}|{class_key}"
    cached = corpus.get_cached(rel_path, "detect_objects", "default", cache_key)
    if cached:
        return cached

    # Sample frame(s).
    from .video import extract_frame, sample_frames

    is_image = corpus.get(rel_path) and corpus.get(rel_path).get("kind") == "image"
    if is_image:
        # Single-shot image — no ffmpeg pass needed.
        frames = [{"at_seconds": 0.0, "at_percent": None, "frame_path": str(abs_path.relative_to(cfg.cache_dir.parent) if str(abs_path).startswith(str(cfg.cache_dir.parent)) else abs_path)}]
    elif at_seconds is not None:
        frame = await extract_frame(
            cfg, corpus, rel_path,
            at_seconds=at_seconds, width=width, return_base64=False,
        )
        frames = [frame]
    else:
        frames = await sample_frames(
            cfg, corpus, rel_path, count=sample_count, width=width,
        )

    # Resolve which classes to filter on (ultralytics takes class IDs, not
    # names). Defer the names→ids lookup until we have a loaded model so the
    # class list is authoritative.
    loop = asyncio.get_running_loop()

    def _run_detect():
        m = _get_model(cfg, model)
        class_filter = None
        if classes:
            name_to_id = {v: k for k, v in m.names.items()}  # type: ignore[union-attr]
            unknown = [c for c in classes if c not in name_to_id]
            if unknown:
                raise ValueError(
                    f"unknown class name(s) for {model}: {unknown}. "
                    f"valid: {sorted(name_to_id)[:10]}... ({len(name_to_id)} total)"
                )
            class_filter = [name_to_id[c] for c in classes]

        per_frame: list[dict[str, Any]] = []
        for f in frames:
            # Resolve the frame path. For sampled frames the path is relative
            # to cfg.cache_dir; for images it's the absolute corpus path.
            if is_image:
                src = abs_path
            else:
                src = cfg.cache_dir / f["frame_path"]
            if not src.is_file():
                log.warning("frame missing on disk: %s", src)
                continue

            kwargs: dict[str, Any] = {
                "conf": confidence,
                "iou": iou,
                "verbose": False,
            }
            if class_filter is not None:
                kwargs["classes"] = class_filter

            results = m.predict(str(src), **kwargs)
            r = results[0]
            detections: list[dict[str, Any]] = []
            if r.boxes is not None and len(r.boxes) > 0:
                names = r.names
                xyxy = r.boxes.xyxy.cpu().tolist()
                confs = r.boxes.conf.cpu().tolist()
                clss = r.boxes.cls.cpu().tolist()
                for box, conf, cls_id in zip(xyxy, confs, clss, strict=False):
                    detections.append({
                        "label": names[int(cls_id)],
                        "confidence": round(float(conf), 3),
                        "bbox": [round(v, 1) for v in box],
                    })

            per_frame.append({
                "at_seconds": f.get("at_seconds"),
                "at_percent": f.get("at_percent"),
                "frame_path": f.get("frame_path"),
                "detections": detections,
                "detection_count": len(detections),
            })
        return per_frame

    per_frame = await loop.run_in_executor(None, _run_detect)

    result: dict[str, Any] = {
        "path": rel_path,
        "model": model,
        "confidence_threshold": confidence,
        "iou_threshold": iou,
        "classes_filter": classes,
        "frame_count": len(per_frame),
        "frames": per_frame,
        "consensus": _aggregate_labels(per_frame),
    }
    corpus.put_cached(rel_path, "detect_objects", "default", cache_key, result)
    return result
