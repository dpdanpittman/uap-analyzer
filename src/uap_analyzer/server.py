"""FastMCP server entry point. Registers tools and serves them over HTTP."""

from __future__ import annotations

import logging
import math
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Config
from .corpus import Corpus, serialize_item
from .tools import audio as audio_tools
from .tools import detect as detect_tools
from .tools import flir as flir_tools
from .tools import image as image_tools
from .tools import pdf as pdf_tools
from .tools import video as video_tools

# MCP-boundary clamps. The new v0.2.x-v0.4.0 inference tools accept
# numerically-bounded params from clients; without server-side caps a single
# call can burn arbitrary CPU / disk / model-call budget on a LAN box that
# has no auth. (Tribunal sec-F-sec-001 + adversary A-002 for the v0.1 surface.)
# Caps are 1-2 orders of magnitude above realistic use so an honest client
# never hits them.
_MAX_SAMPLE_COUNT = 100
_MAX_WIDTH = 4096
_MAX_BEAM_SIZE = 20
_MAX_MAX_SECONDS = 86400  # 24h transcript ceiling
_MAX_FRAME_AT_SECONDS = 86400
_MAX_PDF_DPI = 600              # tesseract default is 200; 600 covers fine OCR
_MAX_PDF_PAGE_INDEX = 100_000   # arbitrary cap on the largest pdf we'd touch
_MAX_TEXT_CHARS = 10_000_000    # 10 MB of extracted text per call


def _bounded(
    name: str,
    value: int | float | None,
    cap: int | float,
    *,
    min_value: int | float = 0,
) -> int | float | None:
    """Validate a client-supplied numeric is in [min_value, cap] and not NaN.

    NaN rejection added in v0.4.2 (adversary A-001): under IEEE-754,
    `0.0 < nan` and `nan > cap` are both False, so the previous bounds
    check silently let NaN through. `width=0` rejection (adversary A-010)
    via the `min_value` parameter, defaulted to 0 for backward-compat with
    optional-int callers and raised to 1 at site for dimension-like args.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        raise ValueError(f"{name} must be a real number; got NaN")
    if value < min_value:
        raise ValueError(f"{name} must be >= {min_value}; got {value}")
    if value > cap:
        raise ValueError(f"{name} must be <= {cap}; got {value}")
    return value

log = logging.getLogger(__name__)


def build_server(cfg: Config | None = None) -> tuple[FastMCP, Config, Corpus]:
    cfg = cfg or Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)

    mcp = FastMCP("uap-analyzer")

    # The container is exposed on the LAN (e.g. http://192.168.6.56:3260) and
    # called from Claude Code running elsewhere. FastMCP's default DNS-rebinding
    # protection only allows localhost/127.0.0.1. Disable it — this server has
    # no auth anyway and the LAN is trusted infrastructure.
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )

    # -----------------------------------------------------------------------
    # list_corpus
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def list_corpus(
        kind: str | None = None,
        filter: str | None = None,
        rescan: bool = False,
    ) -> dict[str, Any]:
        """List indexed corpus files.

        Args:
            kind: 'video', 'pdf', 'image', or None for all.
            filter: Substring match against the relative path.
            rescan: If true, walk data_dir and refresh the index before listing.
        """
        if rescan or not corpus.list():
            scan_summary = corpus.scan()
        else:
            scan_summary = None

        items = [serialize_item(d) for d in corpus.list(kind=kind, filter_substr=filter)]
        return {
            "data_dir": str(cfg.data_dir),
            "count": len(items),
            "fts_indexed": corpus.fts_indexed_count(),
            "scan_summary": scan_summary,
            "items": items,
        }

    # -----------------------------------------------------------------------
    # analyze_video
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def analyze_video(
        path: str,
        mode: str = "metadata",
        count: int = 3,
        width: int = 800,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        """Analyze a video file.

        Args:
            path: Path to the video (relative to UAP_DATA_DIR, or absolute under it).
            mode: 'metadata' (ffprobe), 'frames' (sample N frame paths),
                  'describe' (sample N frames + vision-describe each).
            count: For mode='frames'/'describe', number of frames to sample. Default 3.
            width: For mode='frames'/'describe', output frame width in pixels. Default 800.
            prompt: For mode='describe', override the FLIR-tuned default prompt.
        """
        _bounded("count", count, _MAX_SAMPLE_COUNT, min_value=1)
        _bounded("width", width, _MAX_WIDTH, min_value=1)
        if mode == "metadata":
            return await video_tools.analyze_video_metadata(cfg, corpus, path)
        if mode == "frames":
            return await video_tools.analyze_video_frames(
                cfg, corpus, path, count=count, width=width
            )
        if mode == "describe":
            return await video_tools.analyze_video_describe(
                cfg, corpus, path, count=count, width=width, prompt=prompt
            )
        raise ValueError(f"unknown mode: {mode!r}")

    # -----------------------------------------------------------------------
    # extract_frame
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def extract_frame(
        path: str,
        at_seconds: float | None = None,
        at_percent: float | None = None,
        width: int = 800,
        return_base64: bool = True,
    ) -> dict[str, Any]:
        """Extract a single frame from a video.

        Args:
            path: Video path (relative to UAP_DATA_DIR).
            at_seconds: Timestamp in seconds. Mutually exclusive with at_percent.
            at_percent: Fraction of duration (0.0-1.0). Defaults to 0.25 if both omitted.
            width: Output width in pixels. Height auto-scales.
            return_base64: If true, include base64 JPEG in response.
        """
        _bounded("at_seconds", at_seconds, _MAX_FRAME_AT_SECONDS)
        _bounded("width", width, _MAX_WIDTH, min_value=1)
        if at_percent is not None and not (0.0 <= at_percent <= 1.0):
            raise ValueError(f"at_percent must be in [0, 1]; got {at_percent}")
        # at_percent NaN check (adversary A-001 covers _bounded path; this is the parallel guard)
        if at_percent is not None and isinstance(at_percent, float) and math.isnan(at_percent):
            raise ValueError("at_percent must be a real number; got NaN")
        return await video_tools.extract_frame(
            cfg, corpus, path,
            at_seconds=at_seconds, at_percent=at_percent,
            width=width, return_base64=return_base64,
        )

    # -----------------------------------------------------------------------
    # describe_image
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def describe_image(
        path: str,
        prompt: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Vision-describe an image via the local ollama vision model.

        Accepts either a path under the corpus (e.g. an FBI photo) or a cached
        extracted frame path (e.g. 'frames/DOD_111688723/t1.35_w800_*.jpg').

        Args:
            path: Image path.
            prompt: Optional custom prompt. Defaults: FLIR-tuned for frames, generic for photos.
            model: Optional override of OLLAMA_VISION_MODEL.
        """
        return await image_tools.describe_image(
            cfg, corpus, path, prompt=prompt, model=model
        )

    # -----------------------------------------------------------------------
    # analyze_pdf
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def analyze_pdf(
        path: str,
        mode: str = "metadata",
        max_chars: int = 60_000,
        page_start: int | None = None,
        page_end: int | None = None,
        dpi: int = 200,
    ) -> dict[str, Any]:
        """Analyze a PDF file.

        Args:
            path: PDF path (relative to UAP_DATA_DIR).
            mode: 'metadata' (page count + author/title),
                  'text' (pdfplumber extraction; cheap),
                  'ocr' (tesseract over rasterized pages; for scanned PDFs),
                  'summary' (text → ollama text model).
            max_chars: For text/ocr/summary, cap the extracted text length.
            page_start: For text/ocr, 1-indexed first page.
            page_end: For text/ocr, 1-indexed last page (inclusive).
            dpi: For ocr, rasterization DPI. 200 is a good default; 300 is slower but cleaner.
        """
        # Adversary A-002: the v0.1-era PDF tools were missed by the v0.4.1
        # _bounded() rollout. dpi=10000 on a multi-page PDF allocates gigapixel
        # images per page and OOMs the container. Cap aggressively.
        _bounded("max_chars", max_chars, _MAX_TEXT_CHARS, min_value=1)
        _bounded("page_start", page_start, _MAX_PDF_PAGE_INDEX, min_value=1)
        _bounded("page_end", page_end, _MAX_PDF_PAGE_INDEX, min_value=1)
        _bounded("dpi", dpi, _MAX_PDF_DPI, min_value=72)
        if mode == "metadata":
            return await pdf_tools.analyze_pdf_metadata(cfg, corpus, path)
        if mode == "text":
            return await pdf_tools.analyze_pdf_text(
                cfg, corpus, path,
                max_chars=max_chars, page_start=page_start, page_end=page_end,
            )
        if mode == "ocr":
            return await pdf_tools.analyze_pdf_ocr(
                cfg, corpus, path,
                max_chars=max_chars, page_start=page_start, page_end=page_end, dpi=dpi,
            )
        if mode == "summary":
            return await pdf_tools.analyze_pdf_summary(
                cfg, corpus, path, max_chars=max_chars
            )
        raise ValueError(f"unknown mode: {mode!r}")

    # -----------------------------------------------------------------------
    # search_corpus
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def search_corpus(
        query: str,
        kind: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Full-text search across previously-extracted PDF text.

        Supports FTS5 syntax: AND, OR, NOT, "phrase queries", prefix*. Results are
        ranked by bm25 and include short snippets with the match highlighted («match»).

        IMPORTANT: only PDFs that have been processed via `analyze_pdf(mode='text')`
        or `mode='ocr'` are searchable. Call `index_corpus` to populate the index.

        Args:
            query: FTS5 query string.
            kind: Optional filter ('pdf' is the only kind currently indexed).
            limit: Max hits to return. Default 10.
        """
        hits = corpus.fts_search(query, limit=limit, kind=kind)
        return {
            "query": query,
            "kind_filter": kind,
            "indexed_count": corpus.fts_indexed_count(),
            "hit_count": len(hits),
            "hits": hits,
        }

    # -----------------------------------------------------------------------
    # index_corpus
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def index_corpus(
        kind: str = "pdf",
        max_chars: int = 60_000,
        force: bool = False,
        ocr_fallback: bool = True,
    ) -> dict[str, Any]:
        """Bulk-index PDFs (or other kinds) into the search index.

        For each PDF in the corpus, run `analyze_pdf(mode='text')`; if the text yield
        is poor and `ocr_fallback` is on, also run `mode='ocr'`. Indexed text feeds
        `search_corpus`. Cached results are reused unless `force=True`.

        Args:
            kind: Which kind to index. Default 'pdf'.
            max_chars: Cap on extracted text per file (also caps search index size).
            force: Re-extract even if cached.
            ocr_fallback: If text yield is poor, also OCR.
        """
        # Adversary A-002: bulk path multiplies max_chars by N items; tighter cap.
        _bounded("max_chars", max_chars, _MAX_TEXT_CHARS, min_value=1)
        items = corpus.list(kind=kind)
        indexed = 0
        skipped = 0
        ocrd = 0
        failed: list[dict[str, str]] = []

        for it in items:
            rel = it["path"]
            try:
                if force:
                    # Bust the text cache for this file by writing a sentinel — easier
                    # to just re-run and let put_cached overwrite.
                    pass
                txt = await pdf_tools.analyze_pdf_text(
                    cfg, corpus, rel, max_chars=max_chars
                )
                if txt.get("likely_scanned") and ocr_fallback:
                    txt = await pdf_tools.analyze_pdf_ocr(
                        cfg, corpus, rel, max_chars=max_chars
                    )
                    ocrd += 1
                if txt.get("text") and len(txt["text"]) > 200:
                    indexed += 1
                else:
                    skipped += 1
            except Exception as e:  # noqa: BLE001
                failed.append({"path": rel, "error": str(e)[:200]})

        return {
            "kind": kind,
            "total_items": len(items),
            "indexed": indexed,
            "ocrd": ocrd,
            "skipped_empty": skipped,
            "failed_count": len(failed),
            "failed": failed[:20],
            "fts_indexed_total": corpus.fts_indexed_count(),
        }

    # -----------------------------------------------------------------------
    # flir_hud_ocr
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def flir_hud_ocr(
        path: str,
        mode: str = "ocr",
        at_seconds: float | None = None,
        sample_count: int = 5,
        width: int = 1280,
        regions: list[str] | None = None,
        vision_model: str | None = None,
    ) -> dict[str, Any]:
        """Extract burned-in FLIR HUD overlay fields from a video.

        Extracts canonical FLIR HUD fields (classification stamp, mode, zoom,
        range, bearing, elevation, timecode) and aggregates cross-frame consensus.

        Two extraction modes:
          - 'ocr' (default): tesseract over per-corner crops, then regex-parse.
                  Fast and cheap. Best for clear, high-contrast overlays.
          - 'vision': qwen2.5vl (via ollama) with a structured-JSON prompt.
                  Slower (~10s/frame) but far more accurate on FLIR HUDs where
                  tesseract struggles (anti-aliased fonts, low-contrast IR).

        Two sampling modes:
          - single frame: pass `at_seconds=T` to extract just that timestamp.
          - sampled (default): samples `sample_count` frames evenly.

        Args:
            path: Video path (relative to UAP_DATA_DIR).
            mode: 'ocr' (tesseract) or 'vision' (qwen2.5vl). Default 'ocr'.
            at_seconds: If set, extract a single frame at this timestamp.
            sample_count: Frames to sample when at_seconds is None. Default 5.
            width: Frame width in pixels. Larger = slower but better extraction.
            regions: HUD region keys for ocr mode (ignored in vision mode).
                     Defaults to all corners + top/bottom strips. Valid: top,
                     bottom, top_left, top_right, bottom_left, bottom_right, full.
            vision_model: Override OLLAMA_HUD_MODEL for vision mode (e.g. switch
                          to 'llama3.2-vision:11b' to A/B against the default).
        """
        _bounded("sample_count", sample_count, _MAX_SAMPLE_COUNT, min_value=1)
        _bounded("width", width, _MAX_WIDTH, min_value=1)
        _bounded("at_seconds", at_seconds, _MAX_FRAME_AT_SECONDS)
        return await flir_tools.flir_hud_ocr(
            cfg, corpus, path,
            mode=mode,
            at_seconds=at_seconds,
            sample_count=sample_count,
            width=width,
            regions=regions,
            vision_model=vision_model,
        )

    # -----------------------------------------------------------------------
    # transcribe_audio
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def transcribe_audio(
        path: str,
        model: str | None = None,
        language: str | None = None,
        initial_prompt: str | None = None,
        beam_size: int = 5,
        vad_filter: bool = True,
        max_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Transcribe a video or audio file via faster-whisper (CPU, int8).

        The UAP corpus is mostly video with audio tracks (debriefings, press
        conferences, news segments). This tool extracts the audio, runs it
        through whisper, and returns timestamped segments + full text. Once
        transcripts exist, search_corpus can find them via FTS5 too.

        Models auto-download to HF_HOME on first use (~30-90s for base/small);
        cached forever after. Default model is base.en — fast and good on
        English UAP briefings; bump to small.en or medium.en for clips with
        heavy technical jargon.

        Args:
            path: Video or audio path (relative to UAP_DATA_DIR).
            model: Override WHISPER_MODEL. e.g. "small.en", "medium.en",
                   "distil-large-v3". See faster-whisper docs.
            language: ISO code (e.g. "en"). None = auto-detect.
            initial_prompt: Bias the decoder with vocabulary hints. Useful
                            for technical jargon — pass something like
                            "ATFLIR, AIM-9X, FLIR, AAQ-28, sortie" to seed
                            domain vocab.
            beam_size: Decoder beam width (default 5).
            vad_filter: Skip silent stretches via voice-activity detection.
            max_seconds: Cap transcription at this duration. Useful for
                         previewing long press conferences.
        """
        _bounded("beam_size", beam_size, _MAX_BEAM_SIZE)
        _bounded("max_seconds", max_seconds, _MAX_MAX_SECONDS)
        if initial_prompt is not None and len(initial_prompt) > 1024:
            raise ValueError("initial_prompt must be <= 1024 chars")
        return await audio_tools.transcribe_audio(
            cfg, corpus, path,
            model=model,
            language=language,
            initial_prompt=initial_prompt,
            beam_size=beam_size,
            vad_filter=vad_filter,
            max_seconds=max_seconds,
        )

    # -----------------------------------------------------------------------
    # detect_objects
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def detect_objects(
        path: str,
        at_seconds: float | None = None,
        sample_count: int = 5,
        confidence: float = 0.25,
        iou: float = 0.45,
        classes: list[str] | None = None,
        model: str = "yolov8n",
        width: int = 1280,
    ) -> dict[str, Any]:
        """Run YOLO object detection over sampled frames (or one frame).

        Returns per-timestamp lists of [label, confidence, bbox] detections,
        plus a cross-frame aggregation that counts how often each label
        appeared and ranks the top labels seen across the sweep.

        IMPORTANT: COCO-pretrained YOLO knows 80 ordinary-world classes
        (person, airplane, car, boat, …). FLIR / IR-mode footage shows
        unlabeled thermal blobs that COCO has no category for — expect zero
        detections on those clips. Useful signal comes from daylight footage,
        TV-mode footage, and photo material.

        Args:
            path: Video or image path (relative to UAP_DATA_DIR).
            at_seconds: If set, detect on a single frame at this timestamp.
            sample_count: Frames to sample when at_seconds is None. Default 5.
            confidence: Min confidence to keep a detection. Default 0.25.
            iou: Non-max-suppression IoU threshold. Default 0.45.
            classes: Optional COCO label names to filter to (e.g.
                     ["airplane", "person", "boat"]). None = all 80.
            model: YOLO variant. yolov8n (default, ~6MB, fast) →
                   yolov8x (~136MB, accurate). yolov11* accepted too.
            width: Inference resolution passed to YOLO as `imgsz`. YOLO native
                   is 640; 1280 trades latency for a bit more small-object
                   recall.
        """
        _bounded("sample_count", sample_count, _MAX_SAMPLE_COUNT, min_value=1)
        _bounded("width", width, _MAX_WIDTH, min_value=1)
        _bounded("at_seconds", at_seconds, _MAX_FRAME_AT_SECONDS)
        if classes is not None and len(classes) > 80:
            raise ValueError("classes filter must have at most 80 entries (COCO size)")
        return await detect_tools.detect_objects(
            cfg, corpus, path,
            at_seconds=at_seconds,
            sample_count=sample_count,
            confidence=confidence,
            iou=iou,
            classes=classes,
            model=model,
            width=width,
        )

    return mcp, cfg, corpus
