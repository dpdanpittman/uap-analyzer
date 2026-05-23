"""FastMCP server entry point. Registers tools and serves them over HTTP."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Config
from .corpus import Corpus, serialize_item
from .tools import flir as flir_tools
from .tools import image as image_tools
from .tools import pdf as pdf_tools
from .tools import video as video_tools

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
        return await flir_tools.flir_hud_ocr(
            cfg, corpus, path,
            mode=mode,
            at_seconds=at_seconds,
            sample_count=sample_count,
            width=width,
            regions=regions,
            vision_model=vision_model,
        )

    return mcp, cfg, corpus
