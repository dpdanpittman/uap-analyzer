# Intent — uap-analyzer v0.2.x → v0.4.0 ship

## System Identity

- **Plan ID:** P-uap-v04
- **Scope:** the work shipped between commit `0ce846d` (License: MIT → AGPLv3+) and commit `c267b63` (site refresh). Five release commits across the diff:
  - `962a137` v0.2.0 — `flir_hud_ocr` (tesseract path)
  - `697f693` v0.2.1 — `flir_hud_ocr` mode=vision + deploy hardening
  - `9340baf` v0.3.0 — `transcribe_audio` (faster-whisper)
  - `3c2b62f` v0.4.0 — `detect_objects` (YOLOv8 / ultralytics)
  - `c267b63` — marketing site refresh

The diff is ~1876 insertions, 53 deletions across 20 files. Net new code lives in `src/uap_analyzer/tools/{audio,detect,flir}.py` (~960 lines), plus a wider test surface (`tests/test_smoke.py`), config/server wiring, and deploy hardening (`Dockerfile`, `docker-compose.yml`, `.env.example`, `deploy/zaphod-deploy.sh`).

- **Purpose:** validate that this session's MCP-tool additions are correct, defensible, and don't regress the v0.1 surface. Get adversarial signal on the corpus-aware analysis stack (FLIR HUD extraction, audio transcription, object detection) and the deploy-hardening pass that came out of the live `.env`-deletion incident.

## Behaviors under review

Six behaviors. Each is a claim the audit must confirm or contest.

1. **`tools/flir.py` — `flir_hud_ocr(mode="ocr")` extracts canonical HUD fields via tesseract.**
   - Input: video path, sample_count or at_seconds, optional region whitelist.
   - Expected: returns per-frame `{fields, raw_text, region_texts}` + cross-frame `consensus` keyed by canonical FLIR field names (classification, mode, zoom, range_nm, bearing_deg, elevation_deg, timecode).
   - Edge: numeric fields must validate range (bearing 0..360, elevation -90..90); invalid values silently drop, not coerce. FOV-token zoom (`NAR`/`MED`/`WIDE`) uppercases; numeric zoom keeps `x` prefix.

2. **`tools/flir.py` — `flir_hud_ocr(mode="vision")` routes through qwen2.5vl with structured-JSON output.**
   - Input: same as ocr mode + optional `vision_model` override.
   - Expected: model receives `FLIR_HUD_VISION_PROMPT`, ollama returns JSON-mode response, `_normalize_vision_fields` validates output against the same enum/range set ocr mode uses, both modes produce comparable consensus output.
   - Edge: invalid/out-of-range values from the model are dropped (not coerced). Markdown-fenced JSON is tolerated. Failed parse logs warning + returns empty fields, not error.

3. **`tools/audio.py` — `transcribe_audio` extracts audio + runs faster-whisper.**
   - Input: video or audio path, optional model, language, initial_prompt, vad_filter, max_seconds.
   - Expected: ffmpeg extracts audio to 16 kHz mono WAV in cache (one-shot, reused on re-calls), WhisperModel cached at module scope, results include `segments[]` with timestamps + `full_text` + `language` + `language_probability`. Cache key folds in every param that materially changes output.
   - Edge: unknown model name rejected pre-I/O. Invalid `WHISPER_COMPUTE_TYPE` rejected. `max_seconds` truncates via `clip_timestamps`. `vad_filter=False` is required for the current (silent) UAP corpus; this is documented.

4. **`tools/detect.py` — `detect_objects` runs YOLOv8 over sampled frames.**
   - Input: video or image path, optional confidence/iou/classes/model/width.
   - Expected: returns per-frame `[label, confidence, bbox]` lists + cross-frame `consensus` (total + by_label counter + frames_with_label + top-5 ranked). Model cached at module scope. First call downloads weights to `YOLO_CONFIG_DIR`.
   - Edge: confidence + iou validated in (0, 1]. Unknown YOLO model rejected pre-I/O. Unknown class names rejected with a useful error before inference. Images bypass the frame-sampling path.

5. **Deploy hardening — `docker-compose.yml` default + `deploy/zaphod-deploy.sh`.**
   - Input: missing `.env` on zaphod (the incident that prompted the hardening).
   - Expected: `UAP_HOST_DATA_DIR` default in compose.yaml is `/home/zaphod-beeblebox/uap-data` (NOT `/srv/uap-data`), so a missing `.env` mounts the real corpus.
   - Expected: `deploy/zaphod-deploy.sh` excludes `.env` from the rsync delete set + excludes `__pycache__`, `.venv`, `node_modules`, `dist`, `.git`, `.pytest_cache`, `.astro`. `--no-build` / `--no-restart` flags work as documented. Healthz is verified post-restart.
   - Edge: deploying from a host where `~/src/uap-analyzer/` doesn't exist on the remote → rsync creates the dir.

6. **MCP server wiring — three new tools registered + healthz exposes model state.**
   - Input: container start, hit `/healthz`.
   - Expected: response includes `vision_model`, `hud_model`, `text_model`, `corpus_items`. Each new tool (`flir_hud_ocr`, `transcribe_audio`, `detect_objects`) is registered via `@mcp.tool()` with a docstring and types. Tool dispatch boundary in `server.py` doesn't import inference deps eagerly (heavy imports stay inside the tool modules).
   - Edge: container restart with empty corpus → healthz returns `corpus_items: 0` not 500. Tool surface is forward-compatible: client passing a parameter the server doesn't know about should not crash (MCP layer enforcement, but worth flagging).

## Invariants

- **State:** the `Corpus`'s `cache_dir` is the canonical persistence layer. Each tool that does inference MUST call `corpus.put_cached(path, tool, mode, key, result)` so subsequent calls short-circuit. Cache keys MUST fold in every param that materially changes output.
- **State:** ffmpeg-extracted frames and audio WAVs live under `cache_dir/{frames,audio}/<rel-path-stem>/` and ARE reused across tool calls. The stem-only path means moving the source video to a different directory does NOT invalidate the extracted derivatives.
- **State:** the `WhisperModel` and `YOLO` instances are module-level singletons per (model_name, compute_type). Loading is amortized across calls within a single process lifetime.
- **Temporal:** the structural cache (`items` table in `index.db`) reflects what's on disk **at the time of last `scan()`**. `scan()` is additive — it does NOT prune missing files. (This is a known limitation surfaced by today's Release_1 reorganization; a `prune()` follow-up is documented but not in this diff.)
- **Temporal:** between consecutive container restarts, all model weights (whisper, yolo) and cached frames/audio MUST survive. The bind-mount of `~/uap-data/.cache/` is what makes this true; `HF_HOME` + `YOLO_CONFIG_DIR` in the Dockerfile pin those caches into the mount.
- **Security:** every tool path resolves via `cfg.resolve_corpus_path()` which rejects anything outside `UAP_DATA_DIR`. The new tools (audio/detect/flir) MUST go through this; direct `Path()` resolution would bypass the boundary.
- **Security:** the container runs inference locally via ollama on the host; NO third-party API calls are made. Vision-mode flir_hud_ocr MUST use the local ollama daemon, never reach out to Anthropic/OpenAI.

## Failure modes

- **Tesseract not installed** → `_ocr_region` catches `TesseractNotFoundError`, logs warning, returns empty string. ocr-mode returns 0 fields per frame. Acceptable.
- **Ollama vision model unavailable** for vision-mode → `OllamaClient` raises `OllamaError`; caller catches and stores `{fields: {}, error: str(e)[:240]}` per frame, continues. Acceptable.
- **faster-whisper model download fails** (HF Hub unreachable on first call) → `WhisperModel(...)` raises; tool raises; tool result NOT cached. Subsequent calls retry the download. Acceptable degradation.
- **YOLO weights download fails** → `YOLO(...)` raises; tool raises; tool result NOT cached. Same as whisper.
- **Audio extraction fails** (corrupt video, no audio stream) → `_ffmpeg_to_wav` raises with non-zero ffmpeg exit + stderr tail. Tool surfaces error to caller. Acceptable.
- **Sample frame missing on disk** → tool logs warning and skips that frame. Other frames continue. Result reflects whatever frames did succeed.
- **Vision-mode model returns malformed JSON** → `_normalize_vision_fields` produces an empty dict; per-frame `field_count` is 0; consensus aggregation drops it. Acceptable.
- **Corpus has no audio content** (the observed reality of the current UAP corpus) → `transcribe_audio` returns 0 segments cleanly; consensus is empty; no crash. Acceptable.
- **FLIR clip with no readable HUD** → ocr-mode returns mostly noise; vision-mode returns mostly nulls. Per-frame field_count = 0; consensus is empty. Acceptable.

## Non-goals

- We are NOT auditing the v0.1 base surface (`list_corpus`, `analyze_video`, `extract_frame`, `describe_image`, `analyze_pdf`, `search_corpus`, `index_corpus`). Those are untouched by this diff.
- We are NOT auditing the marketing site copy for factual correctness against the toolchain — the lens-trio's job is code, not marketing claims. (The site refresh in `c267b63` is in scope only structurally: did it break anything?)
- We are NOT proving every vision-model output is correct on real corpus content. The model is a downstream dependency. We ARE auditing that we parse + validate its output defensively.
- We are NOT auditing the on-chain reputation pipeline — uap-analyzer doesn't touch tribunal.
- We are NOT auditing the FastMCP transport security posture — the LAN-trusted disable was a v0.1 decision and is unchanged.

## Trust boundaries

- The MCP server runs on a single host on a trusted LAN. There is no auth on `/mcp` or `/healthz`. Tools accept arbitrary file paths but all go through `cfg.resolve_corpus_path()` which sandboxes to `UAP_DATA_DIR`.
- ollama runs on the host, host-networked into the container. The container trusts the ollama daemon's responses (including the JSON-mode output from vision-mode flir_hud_ocr — bad JSON gets `_normalize_vision_fields`'d into empties, but a deliberately malicious response wouldn't be detected).
- faster-whisper + ultralytics + their model registries (HF Hub, ultralytics GitHub releases) are trusted on first-download. No checksum verification beyond what the libraries do internally.
- All filesystem writes land under `UAP_CACHE_DIR` (in turn under `UAP_DATA_DIR`). Nothing escapes the bind-mount.

## Performance bounds

- `flir_hud_ocr(mode="ocr")`: ~5–10 frames × per-region tesseract ≈ 1–3s per video on the default 5-frame sample. Acceptable; tesseract is the bottleneck.
- `flir_hud_ocr(mode="vision")`: ~10s first call (model load), ~1.5s/frame after. Sampling 5 frames ≈ 8s steady-state. Acceptable for analyst-flow latency.
- `transcribe_audio` (base.en, int8): ~4× realtime on CPU. A 5-minute clip is ~75s of inference + ~2s ffmpeg extract. Acceptable.
- `detect_objects` (yolov8n, int8 implicit via torch CPU): ~150–300ms/frame. 5-frame sample is ~1–1.5s after first load. Acceptable.
- Module-level model caches mean the second call against any tool in the same process is much faster than the first. The container is long-lived so this is usually the case.

## Concrete scenarios

1. **Happy path — full corpus sweep.** Operator calls `list_corpus(rescan=True)` after a Release lands. Then for each video: `flir_hud_ocr(path)` (ocr) → if 0 fields, retry with `mode="vision"`. For audio-bearing clips: `transcribe_audio(path)`. For photos: `detect_objects(path)`. Final state: structural index up to date, FTS index covers all PDFs, every video has cached frames + per-mode HUD-OCR + per-frame detections (where applicable).
2. **Cache reuse under reorganization.** Operator moves `videos/DOD_*.mp4` → `Release_1/DOD_*.mp4` (today's actual operation). Frame cache lives under `cache/frames/DOD_*/` — stem-only paths — so it's reusable. Analysis cache (`analysis_cache` table, keyed by `item_path`) is NOT reused; old entries become orphaned. Expected: tools just re-do inference on the new paths. Verify: no path-related crash; cache eventually saturates again.
3. **Container restart with empty cache.** Operator deletes `~/uap-data/.cache/` and restarts the container. First call to any tool: pays the model-load tax (~10s vision, ~30s whisper download, ~5s YOLO download). All subsequent calls in the same process: fast. After a second container restart, models re-load from the bind-mount, no re-download. This is the v0.3/v0.4 hardening's central claim.
4. **Failed deploy (the .env incident).** Operator runs `deploy/zaphod-deploy.sh`. rsync EXCLUDES `.env` so the deploy-host config survives. compose.yml's `UAP_HOST_DATA_DIR` default points at the real corpus location so the mount works even if `.env` had been deleted. Expected: even a fresh-host deploy that has no `.env` mounts the right path and serves all 220 corpus items via healthz.

The audit must confirm scenarios 1–4 actually hold under the new code OR surface concrete cases where they don't.
