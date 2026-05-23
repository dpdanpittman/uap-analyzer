# uap-analyzer

MCP server that analyzes UAP / Department of War release material. Offloads heavy work (frame extraction, vision-model inference, PDF OCR, full-text search) to a local container so Claude Code conversations don't burn context on raw media.

## What it does

| Tool               | Status    | Purpose                                                                                         |
| ------------------ | --------- | ----------------------------------------------------------------------------------------------- |
| `list_corpus`      | ✅ v1     | List indexed videos / PDFs / photos with metadata                                               |
| `analyze_video`    | ✅ v1     | ffprobe metadata + frame sampling + vision-describe (qwen2-vl)                                  |
| `extract_frame`    | ✅ v1     | Pull a single frame at a timestamp                                                              |
| `analyze_pdf`      | ✅ v1     | pdfplumber text + tesseract OCR fallback + summary via local text model                         |
| `describe_image`   | ✅ v1     | Vision-describe any image via ollama qwen2-vl                                                   |
| `search_corpus`    | ✅ v1     | Full-text search across indexed PDFs (sqlite-fts5, bm25 ranking, FTS5 syntax)                   |
| `index_corpus`     | ✅ v1     | Bulk-index PDFs (with OCR fallback) into the search index                                       |
| `flir_hud_ocr`     | ✅ v0.2.1 | Extract HUD overlay fields. Two modes: `ocr` (tesseract, fast) / `vision` (qwen2.5vl, accurate) |
| `transcribe_audio` | ✅ v0.3.0 | Speech-to-text via faster-whisper (CPU, int8). Segments + full text + auto language detect.     |
| `detect_objects`   | ✅ v0.4.0 | YOLOv8/v11 object detection per frame. CPU torch, weights cached in bind mount.                 |

**Current release:** `v0.4.2` (2026-05-22) — hardening pass driven by two rounds of tribunal review. v0.4.1 closed 12 must-fix items from the first lens trio; v0.4.2 closes 10 more found by the adversary stage (NaN bypass on bounds, unbounded v0.1 tool surface, `describe_image` model whitelist, IMAGE_EXTS expansion, prompt-in-cache-key, ZAPHOD_HOST validation, cache-version unification, shared `hash_key` helper). See `.tribunal/reports/` for the audit trail.

## Architecture

```
Claude Code → MCP HTTP → uap-analyzer container (port 3260) → {ffmpeg, pdfplumber, ollama @ :11434}
                                                            ↓
                                              /srv/uap-data/ (corpus + cache + sqlite)
```

Runs as a Docker container on zaphod (192.168.6.56), colocated with ollama so vision/text inference stays on LAN. Registered with Claude Code via `claude mcp add --transport http`.

## Quickstart (local dev)

```sh
# Install deps
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Point at the corpus
cp .env.example .env
# edit UAP_DATA_DIR=/home/dan/Downloads/uapvideos for local testing

# Run
python -m uap_analyzer
# → MCP server listening on http://0.0.0.0:3260/mcp, healthz at /healthz
```

## Production deploy (zaphod)

```sh
# One-time corpus sync (from Dan's laptop)
./deploy/zaphod-bootstrap.sh

# Build + run on zaphod
ssh zaphod-beeblebox@192.168.6.56 \
  'cd /srv/uap-analyzer && docker compose up -d --build'

# Register with Claude Code
claude mcp add --transport http uap-analyzer http://192.168.6.56:3260/mcp
```

See `deploy/README.md` for the full runbook.

## Repo layout

```
src/uap_analyzer/
  __main__.py           # entrypoint
  server.py             # FastMCP server, tool registration
  config.py             # env config
  corpus.py             # SQLite-backed file index
  tools/
    video.py            # analyze_video, extract_frame
    pdf.py              # analyze_pdf
    image.py            # describe_image (Phase 2)
    ollama_client.py    # ollama HTTP wrapper (Phase 2)
  prompts/
    flir_describe.txt   # canned prompts for FLIR content
deploy/
  zaphod-bootstrap.sh   # one-shot deploy script
  README.md             # deploy runbook
tests/
  test_smoke.py
```

## License

[GNU AGPLv3 or later](./LICENSE). Open-source, copyleft for network use. Personal/analyst use intended; anyone running this as a network service must publish their modifications under the same license.
