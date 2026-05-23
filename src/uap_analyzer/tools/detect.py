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
import threading
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

from ..config import Config
from ..corpus import Corpus
from ._common import CACHE_VERSION, hash_key

log = logging.getLogger(__name__)


# Bounded LRU model cache. Holding a YOLO model in memory is ~6-140MB depending
# on variant. Cap entries so a client A/B-sweeping through variants can't
# exhaust memory; lock the dict so concurrent cold-starts don't duplicate the
# load. (Tribunal perf-F-perf-001/-002/-003.)
_MODEL_CACHE_MAX = 3
_MODEL_CACHE: OrderedDict[str, Any] = OrderedDict()
_MODEL_CACHE_LOCK = threading.Lock()


# Friendly aliases → ultralytics weight filenames. yolov8n (nano, 6.2MB) is the
# default; users can opt up via the `model` arg. yolov11* accepted too.
VALID_MODELS = (
    "yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x",
    "yolov11n", "yolov11s", "yolov11m", "yolov11l", "yolov11x",
)

# Extensions YOLO can run on directly without a frame-extract step.
# v0.4.2: added TIFF, HEIC, AVIF (adversary A-004). TIFF is the canonical
# distribution format for declassified military FLIR stills; HEIC and AVIF
# are increasingly common from modern phone cameras (FBI photo material).
IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
    ".tiff", ".tif", ".heic", ".heif", ".avif",
})


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
    instance — meaningfully faster for repeated detections in one process.

    Thread-safe: the entire get-or-load path is serialized via `_MODEL_CACHE_LOCK`
    so concurrent first-calls (likely under uvicorn's default ThreadPoolExecutor)
    can't duplicate the load or race on the weight download. Cold-start
    serialization is a one-time cost; cached lookups are sub-microsecond.
    """
    if name not in VALID_MODELS:
        # Surface the validation before we even take the lock.
        raise ValueError(f"unknown YOLO model {name!r}; valid: {VALID_MODELS}")

    with _MODEL_CACHE_LOCK:
        if name in _MODEL_CACHE:
            _MODEL_CACHE.move_to_end(name)
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

        # Pass an absolute path to YOLO(). ultralytics will download to the
        # parent directory if the file doesn't exist. This eliminates the
        # previous os.chdir() dance, which was process-global and could leak
        # cwd into other concurrent tool calls during the load window.
        log.info("loading YOLO model %s — first call may download weights", name)
        model = YOLO(str(weight_path))

        _MODEL_CACHE[name] = model
        # Bound the cache. Drop the least-recently-used entry once we exceed.
        while len(_MODEL_CACHE) > _MODEL_CACHE_MAX:
            evicted_name, _ = _MODEL_CACHE.popitem(last=False)
            log.info("LRU-evicted YOLO model %s from cache", evicted_name)

        return model


# `_hash_key` was duplicated across audio/detect/flir in v0.4.1 and had
# already diverged (adversary A-007 / A-008 — flir was at v3 while audio +
# detect were at v2). Consolidated in tools/_common.py for v0.4.2.
_hash_key = hash_key


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
        width: Inference resolution passed to YOLO as `imgsz`. YOLO's native
               training size is 640; 1280 trades latency for a bit more
               recall on small objects. For videos, also the ffmpeg scale
               for sampled frames before they reach the model.
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

    # Classify by extension, NOT by corpus DB lookup. The DB lookup couples
    # correctness to scan state (a never-scanned file would mis-classify) and
    # to exact-string path match (absolute paths under UAP_DATA_DIR would
    # fall through to the ffmpeg branch on PNG inputs). (Tribunal arch-F-arch-002.)
    is_image = abs_path.suffix.lower() in IMAGE_EXTS

    # Cache key folds in every output-affecting param via hash, NOT raw '|'
    # concat (sec-F-sec-005). class_key is sorted-tuple for stable ordering.
    # CACHE_VERSION centralized in _common (adversary A-007).
    class_key = ",".join(sorted(classes)) if classes else "all"
    if at_seconds is not None:
        key_h = _hash_key(
            CACHE_VERSION, model, f"t{at_seconds:.2f}",
            confidence, iou, width, class_key, is_image,
        )
        cache_key = f"{CACHE_VERSION}|t{at_seconds:.2f}|{key_h}"
    else:
        key_h = _hash_key(
            CACHE_VERSION, model, f"n{sample_count}",
            confidence, iou, width, class_key, is_image,
        )
        cache_key = f"{CACHE_VERSION}|n{sample_count}|{key_h}"
    cached = corpus.get_cached(rel_path, "detect_objects", "default", cache_key)
    if cached:
        return cached

    # Sample frame(s). For images we skip ffmpeg entirely; for videos we sample
    # via the existing helpers.
    from .video import extract_frame, sample_frames

    if is_image:
        frames = [{
            "at_seconds": 0.0,
            "at_percent": None,
            # frame_path is unused for the image path — _run_detect uses abs_path
            # directly. Keep the field for the consensus aggregator's shape.
            "frame_path": rel_path,
        }]
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
            # Resolve the frame path. For images, use the absolute corpus path;
            # for sampled frames, the path is relative to cfg.cache_dir.
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
                "imgsz": width,
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
