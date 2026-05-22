"""Video analysis tools."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from ..config import Config
from ..corpus import Corpus
from .image import FLIR_DESCRIBE_PROMPT, describe_image

log = logging.getLogger(__name__)


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out, err


async def ffprobe_video(path: Path) -> dict[str, Any]:
    rc, out, err = await _run(
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_entries",
        "format=duration,bit_rate,format_name,size:stream=codec_name,codec_type,width,height,avg_frame_rate,nb_frames,bit_rate,pix_fmt",
        str(path),
    )
    if rc != 0:
        raise RuntimeError(f"ffprobe failed: {err.decode(errors='replace')[:500]}")

    data = json.loads(out.decode())
    fmt = data.get("format", {})
    vstream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        {},
    )
    astream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )

    fr = vstream.get("avg_frame_rate", "0/0")
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    return {
        "format": fmt.get("format_name"),
        "duration_s": float(fmt.get("duration", 0)) if fmt.get("duration") else None,
        "size_bytes": int(fmt.get("size", 0)) if fmt.get("size") else None,
        "bit_rate": int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else None,
        "codec": vstream.get("codec_name"),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "fps": round(fps, 3) if fps else None,
        "frame_count": int(vstream["nb_frames"]) if vstream.get("nb_frames") else None,
        "pix_fmt": vstream.get("pix_fmt"),
        "has_audio": astream is not None,
        "audio_codec": astream.get("codec_name") if astream else None,
    }


async def analyze_video_metadata(
    cfg: Config, corpus: Corpus, rel_path: str
) -> dict[str, Any]:
    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    cached = corpus.get_cached(rel_path, "analyze_video", "metadata", "v1")
    if cached:
        return cached

    meta = await ffprobe_video(abs_path)
    if meta.get("duration_s") and meta.get("width") and meta.get("height"):
        corpus.update_video_meta(
            rel_path, meta["duration_s"], meta["width"], meta["height"]
        )

    result = {"path": rel_path, "metadata": meta}
    corpus.put_cached(rel_path, "analyze_video", "metadata", "v1", result)
    return result


async def _get_duration(cfg: Config, corpus: Corpus, rel_path: str, abs_path: Path) -> float:
    item = corpus.get(rel_path)
    if item and item.get("duration_s"):
        return float(item["duration_s"])
    meta = await ffprobe_video(abs_path)
    duration = float(meta.get("duration_s") or 0)
    if duration and meta.get("width") and meta.get("height"):
        corpus.update_video_meta(rel_path, duration, meta["width"], meta["height"])
    return duration


async def extract_frame(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    at_seconds: float | None = None,
    at_percent: float | None = None,
    width: int = 800,
    return_base64: bool = True,
) -> dict[str, Any]:
    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    if at_seconds is None and at_percent is None:
        at_percent = 0.25
    if at_seconds is None:
        duration = await _get_duration(cfg, corpus, rel_path, abs_path)
        at_seconds = duration * (at_percent or 0.25)
    at_seconds = max(0.0, float(at_seconds))

    params_hash = hashlib.sha256(f"{at_seconds:.3f}|{width}".encode()).hexdigest()[:16]

    frames_dir = cfg.cache_dir / "frames" / Path(rel_path).with_suffix("").name
    frames_dir.mkdir(parents=True, exist_ok=True)
    out_path = frames_dir / f"t{at_seconds:.2f}_w{width}_{params_hash}.jpg"

    if not out_path.exists():
        rc, _, err = await _run(
            "ffmpeg",
            "-y",
            "-loglevel", "error",
            "-ss", f"{at_seconds:.3f}",
            "-i", str(abs_path),
            "-frames:v", "1",
            "-vf", f"scale={width}:-2",
            "-q:v", "3",
            str(out_path),
        )
        if rc != 0:
            raise RuntimeError(
                f"ffmpeg extract failed: {err.decode(errors='replace')[:500]}"
            )

    rel_frame_path = str(out_path.relative_to(cfg.cache_dir))
    result: dict[str, Any] = {
        "path": rel_path,
        "frame_path": rel_frame_path,
        "at_seconds": round(at_seconds, 3),
        "width": width,
    }
    if return_base64:
        result["jpeg_base64"] = base64.b64encode(out_path.read_bytes()).decode()
    return result


async def sample_frames(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    count: int = 3,
    width: int = 800,
) -> list[dict[str, Any]]:
    """Extract `count` frames at equal intervals through the video.

    Returns a list of frame dicts (no base64 — caller can opt in via extract_frame).
    """
    abs_path = cfg.resolve_corpus_path(rel_path)
    duration = await _get_duration(cfg, corpus, rel_path, abs_path)
    if duration <= 0:
        raise RuntimeError(f"video {rel_path} has unknown duration")

    if count < 1:
        count = 1

    # Sample at (1/(count+1)) ... (count/(count+1)) so we don't hit pure title/end frames.
    frames: list[dict[str, Any]] = []
    for i in range(1, count + 1):
        pct = i / (count + 1)
        at = duration * pct
        f = await extract_frame(
            cfg, corpus, rel_path,
            at_seconds=at, width=width, return_base64=False,
        )
        f["at_percent"] = round(pct, 3)
        frames.append(f)
    return frames


async def analyze_video_frames(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    count: int = 3,
    width: int = 800,
) -> dict[str, Any]:
    """Tool body for analyze_video(mode='frames'): just extract + return paths."""
    cache_key = f"v1|n{count}|w{width}"
    cached = corpus.get_cached(rel_path, "analyze_video", "frames", cache_key)
    if cached:
        return cached

    frames = await sample_frames(cfg, corpus, rel_path, count=count, width=width)
    result = {"path": rel_path, "count": len(frames), "frames": frames}
    corpus.put_cached(rel_path, "analyze_video", "frames", cache_key, result)
    return result


async def analyze_video_describe(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    count: int = 3,
    width: int = 800,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Tool body for analyze_video(mode='describe'): sample frames, describe each.

    Returns per-frame descriptions plus an aggregated string summary.
    """
    prompt_for_cache = prompt or "default-flir"
    prompt_hash = hashlib.sha256(prompt_for_cache.encode()).hexdigest()[:12]
    cache_key = f"v1|n{count}|w{width}|{prompt_hash}"
    cached = corpus.get_cached(rel_path, "analyze_video", "describe", cache_key)
    if cached:
        return cached

    frames = await sample_frames(cfg, corpus, rel_path, count=count, width=width)
    descriptions: list[dict[str, Any]] = []
    for f in frames:
        desc = await describe_image(
            cfg, corpus, f["frame_path"],
            prompt=prompt or FLIR_DESCRIBE_PROMPT,
        )
        descriptions.append(
            {
                "at_seconds": f["at_seconds"],
                "at_percent": f["at_percent"],
                "frame_path": f["frame_path"],
                "description": desc["description"],
                "model": desc["model"],
                "duration_s": desc.get("duration_s"),
            }
        )

    summary = "\n\n".join(
        f"[t={d['at_seconds']}s ({d['at_percent']*100:.0f}%)] {d['description']}"
        for d in descriptions
    )
    result = {
        "path": rel_path,
        "frame_count": len(descriptions),
        "model": descriptions[0]["model"] if descriptions else None,
        "summary": summary,
        "frames": descriptions,
    }
    corpus.put_cached(rel_path, "analyze_video", "describe", cache_key, result)
    return result


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
