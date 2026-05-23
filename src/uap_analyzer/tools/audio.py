"""Audio transcription — extract speech from video/audio files via faster-whisper.

The UAP corpus is mostly video clips with audio tracks: pilot debriefings,
press conferences, news segments accompanying FLIR footage. Pulling those
to text makes the corpus searchable as ordinary documents (search_corpus
already does FTS5; once audio→text lands, transcripts will feed the same
index).

Implementation notes:
- faster-whisper auto-downloads model weights to HF_HOME on first call.
  The Dockerfile sets HF_HOME=/srv/uap-data/.cache/hf so models persist
  across container rebuilds and survive `docker compose down -v`.
- Audio is extracted from the source via ffmpeg into a 16 kHz mono WAV in
  the cache dir, which is what whisper wants. This is cached too — re-runs
  against the same clip skip both the audio extract and the inference.
- CPU inference only. int8 compute type is the default — halves memory,
  ~2× speedup with negligible accuracy loss on UAP-typical English speech.

Conventions per CLAUDE.md:
- All paths are relative to UAP_DATA_DIR.
- All results land in the SQLite cache via `corpus.put_cached`.
- No raw audio bytes returned; only structured JSON with segments + text.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any

from ..config import Config
from ..corpus import Corpus

log = logging.getLogger(__name__)


# Module-level model cache — faster-whisper's WhisperModel is expensive to
# instantiate (loads weights, builds CTranslate2 graph) so we keep one per
# (model_name, compute_type) combo for the lifetime of the process.
_MODEL_CACHE: dict[tuple[str, str], Any] = {}


VALID_MODELS = (
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large-v1", "large-v2", "large-v3",
    "distil-small.en", "distil-medium.en", "distil-large-v2", "distil-large-v3",
)

VALID_COMPUTE_TYPES = ("int8", "int8_float16", "float16", "float32")


async def _ffmpeg_to_wav(
    src: Path, dst: Path, *, sample_rate: int = 16000, channels: int = 1
) -> None:
    """Extract audio from a video/audio file into a 16kHz mono WAV.
    Whisper expects mono 16kHz; any other input gets resampled."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-i", str(src),
        "-ac", str(channels),
        "-ar", str(sample_rate),
        "-vn",          # no video stream
        "-f", "wav",
        str(dst),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg audio extract failed: {err.decode(errors='replace')[:500]}"
        )


def _get_model(model_name: str, compute_type: str):
    """Get-or-create a WhisperModel instance. Cached at module scope so
    repeated tool calls don't pay the load cost."""
    from faster_whisper import WhisperModel  # local import — heavy

    key = (model_name, compute_type)
    if key not in _MODEL_CACHE:
        log.info("loading whisper model %s (compute_type=%s) — first call pays the download + load cost", model_name, compute_type)
        _MODEL_CACHE[key] = WhisperModel(
            model_name,
            device="cpu",
            compute_type=compute_type,
        )
    return _MODEL_CACHE[key]


async def transcribe_audio(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    model: str | None = None,
    language: str | None = None,
    initial_prompt: str | None = None,
    beam_size: int = 5,
    vad_filter: bool = True,
    max_seconds: float | None = None,
) -> dict[str, Any]:
    """Transcribe a video or audio file via faster-whisper.

    Returns segments + full text + detected language + model info.

    Args:
        rel_path: File path relative to UAP_DATA_DIR. Video or audio file.
        model: Override the default WHISPER_MODEL. e.g. "small.en", "medium".
        language: ISO code (e.g. "en"). None = auto-detect.
        initial_prompt: Bias the decoder with domain context (acronyms,
                        unusual names). Useful for technical UAP/aerospace
                        jargon — pass something like "ATFLIR, FLIR, AIM-9X,
                        ATFLIR, ATFLIR, AAQ-28" to teach the model the vocab.
        beam_size: Decoder beam width. 5 is the default; bump to 10 for
                   slightly better accuracy on long clips.
        vad_filter: Whether to skip silence via voice-activity detection.
                    Big wins on clips with long quiet stretches.
        max_seconds: Cap inference at this duration (early-exit). Useful
                     for previewing long press conferences. None = full file.
    """
    model_name = model or cfg.whisper_model
    if model_name not in VALID_MODELS:
        raise ValueError(
            f"unknown whisper model {model_name!r}; valid: {VALID_MODELS}"
        )
    if cfg.whisper_compute_type not in VALID_COMPUTE_TYPES:
        raise ValueError(
            f"WHISPER_COMPUTE_TYPE={cfg.whisper_compute_type!r}; valid: {VALID_COMPUTE_TYPES}"
        )

    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    # Cache key folds in every parameter that materially changes the output.
    key_parts = [
        "v1",
        f"m{model_name}",
        f"l{language or 'auto'}",
        f"b{beam_size}",
        f"v{int(vad_filter)}",
        f"max{int(max_seconds) if max_seconds else 0}",
        hashlib.sha256((initial_prompt or "").encode()).hexdigest()[:8] if initial_prompt else "noinit",
    ]
    cache_key = "|".join(key_parts)
    cached = corpus.get_cached(rel_path, "transcribe_audio", "default", cache_key)
    if cached:
        return cached

    # Extract audio to a 16 kHz mono WAV in the cache dir. Reuse across calls.
    audio_dir = cfg.cache_dir / "audio" / Path(rel_path).with_suffix("").name
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav_path = audio_dir / "16k_mono.wav"
    if not wav_path.exists():
        await _ffmpeg_to_wav(abs_path, wav_path)

    # Run the model. faster-whisper's transcribe() is synchronous — wrap in
    # run_in_executor so we don't block the asyncio loop while CPU inference
    # is running.
    loop = asyncio.get_running_loop()

    def _run_transcribe():
        model_obj = _get_model(model_name, cfg.whisper_compute_type)
        kwargs: dict[str, Any] = {
            "beam_size": beam_size,
            "vad_filter": vad_filter,
        }
        if language:
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        if max_seconds is not None and max_seconds > 0:
            kwargs["clip_timestamps"] = [0.0, float(max_seconds)]

        segments_iter, info = model_obj.transcribe(str(wav_path), **kwargs)
        segs = []
        for s in segments_iter:
            segs.append({
                "start": round(float(s.start), 2),
                "end": round(float(s.end), 2),
                "text": s.text.strip(),
                "no_speech_prob": round(float(getattr(s, "no_speech_prob", 0.0)), 3),
            })
        return segs, info

    segments, info = await loop.run_in_executor(None, _run_transcribe)

    full_text = "\n".join(s["text"] for s in segments if s["text"]).strip()

    result: dict[str, Any] = {
        "path": rel_path,
        "model": model_name,
        "compute_type": cfg.whisper_compute_type,
        "language": info.language,
        "language_probability": round(float(info.language_probability), 3),
        "duration_s": round(float(info.duration), 2),
        "segment_count": len(segments),
        "full_text": full_text,
        "full_text_chars": len(full_text),
        "segments": segments,
    }
    corpus.put_cached(rel_path, "transcribe_audio", "default", cache_key, result)
    return result


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None
