# Plan — uap-analyzer v0.2.x → v0.4.0 ship

## Plan registry

- **ID:** P-uap-v04
- **State:** InReview
- **Owner:** dpdanpittman
- **Working branch:** main
- **Review cwd:** ~/src/uap-analyzer
- **Review range / Diff basis:** `0ce846d..HEAD` (HEAD = `c267b63`)
- **Acceptance criteria:** see intent.md §"Behaviors under review". All Critical = 0, Warning = 0 (unresolved) is the precondition for Approve.

## What ships in this plan

1. **v0.2.0 — `flir_hud_ocr` (tesseract)**
   - New tool `tools/flir.py` with regex-parser + cross-frame consensus aggregator.
   - Per-corner region cropping (6 named regions + full-frame).
   - Tesseract preprocessing: greyscale → autocontrast → 2× upscale → PSM 11 + OEM 1.
   - Wired into `server.py` as `flir_hud_ocr(path, mode, at_seconds, sample_count, width, regions)`.
   - 7 unit tests covering parser + consensus + region validation.

2. **v0.2.1 — `flir_hud_ocr` mode="vision" + deploy hardening**
   - `_vision_extract_frame` sends each frame to qwen2.5vl with `FLIR_HUD_VISION_PROMPT` + ollama `format: "json"`.
   - `_normalize_vision_fields` validates model output against the same enum/range set as ocr mode.
   - New `OLLAMA_HUD_MODEL` env var (default `qwen2.5vl:7b`); surfaced in healthz.
   - **Operational fix:** `UAP_HOST_DATA_DIR` default in compose.yml is `/home/zaphod-beeblebox/uap-data` (was `/srv/uap-data` → empty mount when `.env` missing). Added `deploy/zaphod-deploy.sh` idempotent script with pinned excludes. `.env.example` refreshed.

3. **v0.3.0 — `transcribe_audio` (faster-whisper)**
   - New tool `tools/audio.py`.
   - ffmpeg extracts audio to 16 kHz mono WAV in cache (reused across calls).
   - Module-level WhisperModel cache per (model_name, compute_type).
   - `HF_HOME` env in Dockerfile points at bind-mounted cache so model weights persist.
   - 3 unit tests covering model + compute_type validation.

4. **v0.4.0 — `detect_objects` (YOLOv8 / ultralytics)**
   - New tool `tools/detect.py`.
   - Module-level YOLO model cache per variant.
   - `YOLO_CONFIG_DIR` env in Dockerfile pins weight cache to bind-mount.
   - Dockerfile installs CPU-only torch from pytorch's CPU index BEFORE the project install (avoids multi-GB CUDA wheel).
   - 5 unit tests covering parameter validation + label aggregation.

5. **Site refresh (`c267b63`)**
   - Standalone site (`site/`): tools page now shows 10 tools with version badges; index landing card shows v0.4.0.
   - Hub page (`mabus.ai/src/pages/uap.astro` — not in this repo, separate commit on mabus.ai repo): version stamp + 10-tool surface.

## Out of scope for this review

- The v0.1 base tools — untouched by the diff.
- The cosmic-horizon / mabus-os / session-loam / tribunal-itself work that happened in the same session. Each has its own review path.
- The corpus directory reorganization (today's Release_1 fold-in) — that's data, not code.

## Review dispatch

Lens stage routed via clawpatch (`--via-clawpatch`). Adversary stage uses the default panel (Anthropic Claude). Findings → `.tribunal/ledger.jsonl`, reports → `.tribunal/reports/P-uap-v04/`.
