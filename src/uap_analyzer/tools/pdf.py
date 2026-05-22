"""PDF analysis tools: metadata, text extraction, OCR, summary."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from pypdf import PdfReader

from ..config import Config
from ..corpus import Corpus
from .ollama_client import OllamaClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# metadata + text
# ---------------------------------------------------------------------------


def _read_metadata_sync(abs_path: Path) -> dict[str, Any]:
    reader = PdfReader(str(abs_path))
    info = reader.metadata or {}
    return {
        "page_count": len(reader.pages),
        "title": info.get("/Title"),
        "author": info.get("/Author"),
        "subject": info.get("/Subject"),
        "creator": info.get("/Creator"),
        "producer": info.get("/Producer"),
        "creation_date": str(info.get("/CreationDate") or ""),
        "is_encrypted": reader.is_encrypted,
    }


def _read_text_sync(
    abs_path: Path,
    *,
    max_chars: int,
    page_range: tuple[int, int] | None,
) -> dict[str, Any]:
    pages_text: list[str] = []
    total_chars = 0
    truncated = False
    extracted_pages = 0

    with pdfplumber.open(str(abs_path)) as pdf:
        page_count = len(pdf.pages)
        start, end = page_range if page_range else (1, page_count)
        start = max(1, start)
        end = min(page_count, end)

        for i in range(start - 1, end):
            page = pdf.pages[i]
            text = page.extract_text() or ""
            pages_text.append(text)
            extracted_pages += 1
            total_chars += len(text)
            if total_chars >= max_chars:
                truncated = True
                break

    joined = "\n\n".join(pages_text)
    if len(joined) > max_chars:
        joined = joined[:max_chars]
        truncated = True

    avg_chars_per_page = total_chars / max(1, extracted_pages)
    likely_scanned = avg_chars_per_page < 50 and extracted_pages >= 2

    return {
        "page_count": page_count,
        "pages_extracted": extracted_pages,
        "page_range": [start, end],
        "char_count": total_chars,
        "truncated": truncated,
        "likely_scanned": likely_scanned,
        "text": joined,
    }


async def analyze_pdf_metadata(
    cfg: Config, corpus: Corpus, rel_path: str
) -> dict[str, Any]:
    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    cached = corpus.get_cached(rel_path, "analyze_pdf", "metadata", "v1")
    if cached:
        return cached

    meta = await asyncio.to_thread(_read_metadata_sync, abs_path)
    corpus.update_pdf_meta(rel_path, meta["page_count"])

    result = {"path": rel_path, "metadata": meta}
    corpus.put_cached(rel_path, "analyze_pdf", "metadata", "v1", result)
    return result


async def analyze_pdf_text(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    max_chars: int = 60_000,
    page_start: int | None = None,
    page_end: int | None = None,
) -> dict[str, Any]:
    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    page_range = (page_start, page_end) if (page_start or page_end) else None
    cache_key = f"v1|mc{max_chars}|ps{page_start}|pe{page_end}"
    cached = corpus.get_cached(rel_path, "analyze_pdf", "text", cache_key)
    if cached:
        return cached

    result = await asyncio.to_thread(
        _read_text_sync, abs_path, max_chars=max_chars, page_range=page_range
    )
    result["path"] = rel_path
    corpus.update_pdf_meta(rel_path, result["page_count"])
    corpus.put_cached(rel_path, "analyze_pdf", "text", cache_key, result)

    # Auto-index for search_corpus if there's meaningful text.
    if result.get("text") and len(result["text"]) > 200:
        corpus.fts_upsert(rel_path, result["text"])
    return result


# ---------------------------------------------------------------------------
# OCR (for scanned PDFs)
# ---------------------------------------------------------------------------


def _ocr_pages_sync(
    abs_path: Path,
    *,
    max_chars: int,
    page_range: tuple[int, int] | None,
    dpi: int,
) -> dict[str, Any]:
    # Determine page bounds.
    reader = PdfReader(str(abs_path))
    page_count = len(reader.pages)
    if page_range:
        start, end = page_range
    else:
        start, end = 1, page_count
    start = max(1, start)
    end = min(page_count, end)

    pages_text: list[str] = []
    total_chars = 0
    truncated = False
    pages_ocrd = 0

    # pdf2image will convert one page at a time so we don't blow memory on big PDFs.
    for pno in range(start, end + 1):
        images = convert_from_path(
            str(abs_path), dpi=dpi, first_page=pno, last_page=pno
        )
        if not images:
            continue
        text = pytesseract.image_to_string(images[0]) or ""
        pages_text.append(text)
        pages_ocrd += 1
        total_chars += len(text)
        if total_chars >= max_chars:
            truncated = True
            break

    joined = "\n\n".join(pages_text)
    if len(joined) > max_chars:
        joined = joined[:max_chars]
        truncated = True

    return {
        "page_count": page_count,
        "pages_ocrd": pages_ocrd,
        "page_range": [start, end],
        "char_count": total_chars,
        "truncated": truncated,
        "dpi": dpi,
        "text": joined,
    }


async def analyze_pdf_ocr(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    max_chars: int = 60_000,
    page_start: int | None = None,
    page_end: int | None = None,
    dpi: int = 200,
) -> dict[str, Any]:
    abs_path = cfg.resolve_corpus_path(rel_path)
    if not abs_path.is_file():
        raise FileNotFoundError(rel_path)

    page_range = (page_start, page_end) if (page_start or page_end) else None
    cache_key = f"v1|mc{max_chars}|ps{page_start}|pe{page_end}|dpi{dpi}"
    cached = corpus.get_cached(rel_path, "analyze_pdf", "ocr", cache_key)
    if cached:
        return cached

    result = await asyncio.to_thread(
        _ocr_pages_sync,
        abs_path,
        max_chars=max_chars,
        page_range=page_range,
        dpi=dpi,
    )
    result["path"] = rel_path
    corpus.update_pdf_meta(rel_path, result["page_count"])
    corpus.put_cached(rel_path, "analyze_pdf", "ocr", cache_key, result)

    if result.get("text") and len(result["text"]) > 200:
        corpus.fts_upsert(rel_path, result["text"])
    return result


# ---------------------------------------------------------------------------
# summary (text model)
# ---------------------------------------------------------------------------


SUMMARY_SYSTEM = (
    "You are summarizing a US government document, often a UAP-related FBI / DoW / "
    "FOIA release. Many such documents are dry, redacted, or partially OCRd. "
    "Produce a concise summary covering: (1) document type and date range, (2) the "
    "key incidents/witnesses/locations described, (3) any specific dates, "
    "coordinates, or unit/agency names mentioned, (4) any noteworthy claims or "
    "conclusions. Keep under 400 words. Use plain prose, no headers. If most of "
    "the document is illegible (OCR garbage / heavy redaction), say so explicitly."
)


async def analyze_pdf_summary(
    cfg: Config,
    corpus: Corpus,
    rel_path: str,
    *,
    max_chars: int = 30_000,
) -> dict[str, Any]:
    """Summarize a PDF via ollama text model. Prefers extracted text; falls back to OCR
    if the text yield was empty/poor.
    """
    cache_key = f"v1|mc{max_chars}|{cfg.ollama_text_model}"
    cached = corpus.get_cached(rel_path, "analyze_pdf", "summary", cache_key)
    if cached:
        return cached

    # Try text first.
    text_result = await analyze_pdf_text(
        cfg, corpus, rel_path, max_chars=max_chars
    )
    text_blob = text_result.get("text") or ""
    source = "pdfplumber"

    if not text_blob.strip() or text_result.get("likely_scanned"):
        # OCR fallback.
        ocr_result = await analyze_pdf_ocr(
            cfg, corpus, rel_path, max_chars=max_chars
        )
        text_blob = ocr_result.get("text") or text_blob
        source = "ocr+pdfplumber"

    if not text_blob.strip():
        return {
            "path": rel_path,
            "summary": "No extractable text in this document.",
            "source": source,
        }

    client = OllamaClient(cfg)
    try:
        res = await client.text_chat(
            prompt=f"Document to summarize:\n\n{text_blob}",
            system=SUMMARY_SYSTEM,
            temperature=0.2,
            max_tokens=900,
        )
    finally:
        await client.aclose()

    result = {
        "path": rel_path,
        "summary": res["content"].strip(),
        "source": source,
        "char_count": len(text_blob),
        "model": res["model"],
        "tokens": res["eval_count"],
        "duration_s": round(res["total_duration_s"], 2),
    }
    corpus.put_cached(rel_path, "analyze_pdf", "summary", cache_key, result)
    return result
