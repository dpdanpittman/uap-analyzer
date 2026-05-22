# uap-analyzer

MCP server that analyzes UAP / Department of War release material. Offloads heavy work (frame extraction, vision-model inference, PDF OCR, full-text search) to a local container so Claude Code conversations don't burn context on raw media.

## What it does

| Tool               | Status                   | Purpose                                                             |
| ------------------ | ------------------------ | ------------------------------------------------------------------- |
| `list_corpus`      | ✅ v1                    | List indexed videos / PDFs / photos with metadata                   |
| `analyze_video`    | ✅ v1 (metadata, frames) | ffprobe metadata, frame extraction; describe via vision model in v2 |
| `extract_frame`    | ✅ v1                    | Pull a single frame at a timestamp                                  |
| `analyze_pdf`      | ✅ v1 (metadata, text)   | pdfplumber text extraction; OCR fallback in v2                      |
| `describe_image`   | ⏳ v2                    | Vision-describe any image via ollama qwen2-vl                       |
| `search_corpus`    | ⏳ v2                    | Full-text search across PDFs (sqlite-fts5)                          |
| `audio_transcribe` | ⏳ v3                    | whisper transcripts                                                 |
| `detect_objects`   | ⏳ v3                    | YOLO per-frame                                                      |
| `flir_hud_ocr`     | ⏳ v3                    | Extract HUD metadata from FLIR videos                               |

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

MIT. Personal/analyst use.
