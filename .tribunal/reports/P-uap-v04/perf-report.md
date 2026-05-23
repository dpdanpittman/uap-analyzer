# Tribunal Lens Review — Performance · P-uap-v04

**Reviewer:** tribunal-reviewer-perf
**Verdict:** Request Changes

## Summary

The v0.2.x → v0.4.0 diff lands three useful inference tools, and the steady-state perf envelope in intent.md §"Performance bounds" looks plausible for the single-caller, container-long-lived assumption. However the diff carries one Critical (`os.chdir` in `detect._get_model` is a process-global side effect that races under any concurrent tool dispatch, and the YOLO weight download is the worst time for it to race) and several Warnings around unbounded module-level caches (`_MODEL_CACHE` in both `audio.py` and `detect.py` will grow without bound on adversarial / repeated-with-different-model traffic), `OllamaClient` connect-timeout semantics (a downed ollama daemon hangs each vision call for the full 300s), and per-frame OllamaClient construction in `flir.py` vision mode. Most findings have one-line defenses; none of them rewrite the architecture.

## Findings

### F-perf-001 — `os.chdir` in YOLO model loader is a process-global race

- **Severity:** Critical
- **Location:** `src/uap_analyzer/tools/detect.py:84-90`
- **Workload that triggers:** Two `detect_objects` calls with different models (e.g. `yolov8n` and `yolov8s`) dispatched in quick succession on a cold cache, OR any other tool's relative-path subprocess call (ffmpeg/ffprobe currently use absolute paths so they're safe, but this is a footgun for future relative-path code). Critically: `_get_model` runs inside `loop.run_in_executor(None, _run_detect)` — the default thread pool — so two concurrent first-call requests will both enter the `_get_model` body and both will `os.chdir(weight_dir)` and both will trigger an ultralytics download into the same directory. ultralytics caches a partial download to `<name>.pt.tmp` and renames on success; concurrent downloads can corrupt this.
- **What blows up:** (a) the process cwd is mutated globally for the duration of model load — any other thread doing relative-path resolution during that window resolves against the weight dir; (b) the `_MODEL_CACHE` check is not atomic with the load → both threads pay the download cost twice and the second `_MODEL_CACHE[name] = model` write overwrites the first; (c) on download corruption, both calls raise and the model is left absent for the next call too.
- **Suggested defense:** Replace `os.chdir(weight_dir)` with passing the explicit weight path to `YOLO(weight_path)` (ultralytics accepts an absolute path and will download to that exact location); wrap the `_MODEL_CACHE` check+insert in a `threading.Lock` guarded singleton-load.

### F-perf-002 — Module-level model caches are unbounded; client-supplied model name = uncapped memory growth

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/detect.py:40` (`_MODEL_CACHE: dict[str, Any]`); `src/uap_analyzer/tools/audio.py:43` (`_MODEL_CACHE: dict[tuple[str, str], Any]`)
- **Workload that triggers:** Caller iterates through models for A/B comparison: `for m in ("yolov8n","yolov8s","yolov8m","yolov8l","yolov8x"): detect_objects(path, model=m)`. Each variant stays resident: yolov8n 6 MB → yolov8s 22 MB → yolov8m 52 MB → yolov8l 87 MB → yolov8x 136 MB ≈ 300 MB RAM, in addition to torch's own activation buffers. Same exposure on whisper: tiny → large-v3 is ~75 MB → 3 GB. Container memory is bounded; OOM-killer eviction looks like a "the server randomly restarted" symptom with no log line about which model load tipped it.
- **What blows up:** Unbounded RSS growth. There's no LRU, no `_MODEL_CACHE.clear()` path, no metric reporting cache occupancy.
- **Suggested defense:** Cap `_MODEL_CACHE` to 1 entry (the system can only run one inference at a time anyway on a CPU-bound config) and evict the previous instance when a new one is requested; OR add an explicit env-var-controlled cap (`UAP_MODEL_CACHE_MAX=2`) and a `last-used` map; OR document that the cache is unbounded and rely on caller discipline. The first option is cheapest.

### F-perf-003 — Concurrent first-call to the same model loads it twice

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/detect.py:60-93`; `src/uap_analyzer/tools/audio.py:83-96`
- **Workload that triggers:** Two MCP clients (or one client opening two MCP calls in parallel) hit `transcribe_audio(path=X)` and `transcribe_audio(path=Y)` while `_MODEL_CACHE` is empty. Both calls land in `loop.run_in_executor(None, _run_transcribe)` → default `ThreadPoolExecutor` (≥ CPU-count workers) → both threads execute `_get_model` concurrently → both see `key not in _MODEL_CACHE` → both build a `WhisperModel` (full HF download on cold cache; full CT2 graph build on warm cache). The second write to `_MODEL_CACHE[key]` wins, the first instance is orphaned and slowly GC'd.
- **What blows up:** Wasted CPU + memory (transient 2× during the race), wasted bandwidth (transient 2× HF download during the race), and the first request observes the slower of the two load paths.
- **Suggested defense:** Guard `_get_model` with `threading.Lock` — recheck `_MODEL_CACHE` after acquiring the lock. Same pattern in both files. The lock is uncontended in the steady state so it's a free correctness fix.

### F-perf-004 — `OllamaClient` per-frame construct/teardown in vision-mode FLIR

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/flir.py:292-311`
- **Workload that triggers:** `flir_hud_ocr(mode="vision", sample_count=N)` with N ≥ 2. Each frame builds a fresh `OllamaClient` (which constructs a fresh `httpx.AsyncClient`), makes one request, and `aclose()`s — so each frame pays TCP-handshake + httpx pool-init cost. On localhost the handshake is cheap (sub-ms), but the httpx async client teardown is not free either, and you lose the keepalive/HTTP-pipelining headers that ollama actually honors.
- **What blows up:** 5-frame vision sweep does 5 connection setups instead of 1. Not catastrophic on a localhost loop; meaningful if the daemon is on a different host (the configured default is `192.168.6.56:11434`, host-network into the container, so this is one routing hop).
- **Suggested defense:** Lift the `OllamaClient` to a function-scoped or module-scoped lifecycle inside `flir_hud_ocr` — build once, reuse for all N frames, close once at the end of the call. The keepalive will be honored across frames.

### F-perf-005 — `httpx.AsyncClient(timeout=cfg.ollama_timeout)` makes connect-timeout = 300s

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/ollama_client.py:29`
- **Workload that triggers:** Ollama daemon is down (crashed, restarting, OOM-killed by the host). Any `flir_hud_ocr(mode="vision")`, `describe_image`, `analyze_video(mode="describe")`, or `analyze_pdf(mode="summary")` call now waits the full 300s before raising. The intent doc claims `OLLAMA_TIMEOUT` is enforced and the failure mode is "OllamaClient raises OllamaError, caller catches" — but this assumes a fail-fast on connect. `httpx.AsyncClient(timeout=N)` applies the same value to `connect`, `read`, `write`, and `pool` phases, so a downed daemon doesn't trigger a fast connect-refused, it sits in the connect phase for the full envelope.
- **What blows up:** A degraded ollama becomes 5-minute hangs per tool call. With per-frame vision extraction, that's 5×300s = 25 minutes per `flir_hud_ocr(mode="vision")` against a down daemon (because each frame's `_vision_extract_frame` builds a fresh client — see F-perf-004 — and each retries the connect).
- **Suggested defense:** Pass `httpx.Timeout(connect=5.0, read=cfg.ollama_timeout, write=cfg.ollama_timeout, pool=cfg.ollama_timeout)` instead of the bare int. Connect failures surface in 5s; long inference reads still get the configured envelope.

### F-perf-006 — Vision-mode FLIR loop is sequential; no `asyncio.gather`

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/flir.py:443-463`
- **Workload that triggers:** `flir_hud_ocr(mode="vision", sample_count=5)`. The plan claims ~1.5s/frame steady-state and ~8s total. Currently the 5 frames are awaited sequentially inside a `for` loop, so total = 5 × per-frame. Ollama serializes on a single model anyway (qwen2.5vl on one GPU/CPU can only run one vision pass at a time), so concurrency gains are bounded by the daemon's actual parallelism (typically 1). With `OLLAMA_NUM_PARALLEL>1` on a multi-GPU host this would help; on the default single-stream config it doesn't.
- **What blows up:** Nothing today, but the headroom for future ollama scaling is left on the floor. Also: a `gather` would let you cap total wall-time with `asyncio.wait_for(gather, timeout=N)`.
- **Suggested defense:** Defer until ollama's parallelism is actually configured > 1; document the bound. No code change needed yet.

### F-perf-007 — `transcribe_audio` cached result contains full transcript; long press conferences = bloated cache rows

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/audio.py:199-212`
- **Workload that triggers:** A 90-minute press conference. Whisper at base.en yields ~500-900 segments. Each segment has `text` plus the `full_text` join is stored in full. The result dict is JSON-serialized into `corpus.put_cached`'s `analysis_cache` row in SQLite. Per intent.md §State, cache is the canonical persistence layer — the row size grows linearly with audio duration. A 90-min conference at 0.15 chars/sec spoken ≈ 80 KB transcript; not huge per row, but 220 corpus items × 80 KB ≈ 17 MB in a single SQLite column. SQLite handles it; FTS indexing of these later (the documented next step) will need to chunk.
- **What blows up:** SQLite cache row size; future `search_corpus` over transcripts will need a paged interface.
- **Suggested defense:** Document the row-size envelope in the tool docstring; consider gzipping the `full_text` field at cache-write time once row sizes exceed, say, 100 KB. Not urgent.

### F-perf-008 — `_preprocess` runs per-region per-frame; 6× redundant greyscale+autocontrast

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/flir.py:125-138` (`_ocr_region`) called 6× from `flir_hud_ocr` line 425 per frame
- **Workload that triggers:** Default `flir_hud_ocr(mode="ocr")` with 6 regions × 5 frames = 30 invocations of `_preprocess`. Each does: PIL crop → convert("L") → autocontrast → 2× LANCZOS resize on the cropped region. The crop-then-greyscale order means autocontrast is computed independently per region (which is arguably correct — regional contrast hint — but the greyscale conversion is duplicated work).
- **What blows up:** ~30 PIL ops where 5 + 6 cheap crops would suffice. Maybe 100ms saved per video. Tesseract is the dominant cost (per intent.md "tesseract is the bottleneck"), so this is in the noise.
- **Suggested defense:** Greyscale + autocontrast the full frame once per frame; then crop the preprocessed frame for each region; then 2× upscale the region. Saves N-1 redundant greyscales per frame. Suggestion-only.

### F-perf-009 — `sample_frames` spawns N ffmpeg subprocesses sequentially

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/video.py:168-199`
- **Workload that triggers:** Any of `flir_hud_ocr`/`detect_objects`/`analyze_video(mode=describe)` with `sample_count=5`. Each frame extraction is a fresh ffmpeg subprocess (`-ss T -i src -frames:v 1 -vf scale -q:v 3`), serialized via `await extract_frame(...)` in the for-loop. ffmpeg's fast seek (`-ss` before `-i`) keyframe-snaps so each call is cheap on a streaming-friendly container (mp4, mov), but you're still paying process spawn × N.
- **What blows up:** 5-frame sample = 5 subprocess starts + 5 codec inits. On the corpus (mp4 H.264), each is ~50-150ms; total ~500-750ms of pure overhead. ffmpeg's `select=` filter + `-vsync vfr` could do all 5 frames in one process.
- **Suggested defense:** Add a `_sample_frames_batched()` helper that uses `ffmpeg ... -vf "select='eq(n,F1)+eq(n,F2)+..." -vsync vfr` and emits N output files in one process. Defer until measurement shows it matters. The current per-frame approach is also nice for partial-failure recovery (one bad keyframe doesn't kill the whole sample) — note that trade-off in the new code.

### F-perf-010 — `_get_model` first-call logs once, then silence — model download looks like a hang

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/audio.py:90`; `src/uap_analyzer/tools/detect.py:87`
- **Workload that triggers:** Container starts, HF cache is cold (or operator clears `~/uap-data/.cache/`). First `transcribe_audio` call → `WhisperModel(model_name)` triggers a download. With `small.en` (~250 MB) on a 10 MB/s pipe → 25s. The single `log.info("loading whisper model %s — first call pays the download + load cost")` line is the only signal; from outside it looks like the tool is hung.
- **What blows up:** Operator UX. Same for YOLO weights download (~6-136 MB depending on variant), same for ollama-side model pulls (which the audit doesn't own but does interact with).
- **Suggested defense:** Wrap the model-load call with a wall-clock measurement and log start/elapsed: `log.info("whisper %s loaded in %.1fs", model_name, elapsed)`. Suggest in the README that operators pre-warm the cache via a one-shot call before serving real traffic. For ultralytics, set `progress=True` and let it log per-percent (already its default).

## Cross-Reviewer Ready Notes

- **For architecture reviewer:** F-perf-001 (`os.chdir`) and F-perf-003 (no lock around `_MODEL_CACHE`) are partly architectural concerns about how the new tools coexist with the async dispatch model. The pattern of "lazy-load at first call, cache forever" assumes single-threaded entry; the actual entry surface (`loop.run_in_executor(None, ...)`) is multi-threaded. Worth a design-level pass on whether the singleton-per-process model belongs at module scope or on the `Corpus`/`Config` long-lived objects with proper lifecycle management.
- **For architecture reviewer:** `OllamaClient` per-call construction (F-perf-004) repeats across `flir.py`, `image.py`, `pdf.py`. Centralizing an app-scoped `OllamaClient` singleton on `Config` (similar to `Corpus`) is the natural refactor. Cross-cutting; flagging for arch lens.
- **For security reviewer:** F-perf-001's `os.chdir` race is a security-adjacent concern too. If a future tool ever resolves user-supplied paths relatively during the window when cwd is the weight directory, it would bypass the `cfg.resolve_corpus_path()` sandbox. Today no code does this — but it's a latent footgun worth a security note.
- **For security reviewer:** F-perf-005 (connect-timeout = 300s) has a security/availability angle: a single MCP client (the LAN is trusted but the MCP layer has no auth) could fan out vision-mode calls to a downed ollama, holding open connections and httpx clients for 5 min × N. Resource-exhaustion vector even on a trusted LAN. Worth confirming the sec-lens view.
