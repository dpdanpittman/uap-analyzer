# Tribunal Lens Review — Architecture · P-uap-v04

**Reviewer:** tribunal-reviewer-arch
**Verdict:** Request Changes

## Summary

The diff respects the layering rules well — heavy inference deps (torch/ultralytics, faster-whisper, pytesseract, PIL, ollama_client) are all imported lazily inside functions, so server boot stays light (`src/uap_analyzer/tools/{flir,audio,detect}.py` top-level imports are pure stdlib + Config/Corpus). All three new tools route through `cfg.resolve_corpus_path()` and persist via `corpus.put_cached`, honouring the v0.1 boundary. The MCP `@mcp.tool()` decorator wiring in `server.py:287-432` is clean and the docstrings match the parameter list. However, two contract-conformance defects fall out of the audit: `audio.py:144-154` omits `compute_type` from the cache key (intent §"Invariants" promises "every param that materially changes output" is folded in), and `detect.py:169-172` mis-classifies images via a corpus-DB lookup that silently fails on un-indexed or absolute paths. A handful of warnings/suggestions follow.

## Findings

### F-arch-001 — `transcribe_audio` cache key omits `compute_type`

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/audio.py:144-154`
- **Hunk anchor:** intent.md §Invariants: "Cache keys MUST fold in every param that materially changes output"; plan.md §"What ships" #3 ("`WhisperModel cache per (model_name, compute_type)`").
- **What:** The key_parts list folds in `model`, `language`, `beam_size`, `vad_filter`, `max_seconds`, `initial_prompt`, but NOT `cfg.whisper_compute_type`. Yet the module-level `_MODEL_CACHE` is explicitly keyed by `(model_name, compute_type)` (`audio.py:43, 88`), proving compute_type changes the loaded inference graph. If the operator switches `WHISPER_COMPUTE_TYPE` between `int8` and `float16`/`float32` across container restarts (or per-deploy), `corpus.get_cached(...)` will return the previous compute-type's transcript on a key collision — exactly the regression the cache-key invariant exists to prevent.
- **Why:** Contract conformance — the public surface promises that "subsequent calls short-circuit only when params are identical" (intent §Invariants), and compute_type materially changes segment text (precision differences accumulate in beam search). The fact that the model cache key already includes compute_type while the result cache key does not is the smoking gun.
- **Suggested defense:** Add `f"ct{cfg.whisper_compute_type}"` to `key_parts` in `audio.py:144-154`.

### F-arch-002 — `detect_objects` image classification depends on corpus index, not extension

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/detect.py:169-172`
- **Hunk anchor:** intent §"Behaviors under review" #4: "Images bypass the frame-sampling path." Plan.md §"What ships" #4: "Accepts both videos and standalone images."
- **What:** The `is_image` decision is `corpus.get(rel_path) and corpus.get(rel_path).get("kind") == "image"`. This relies on the SQLite `items` table having been populated by `scan()` AND on `rel_path` matching the stored relative path exactly. Two breakage scenarios:
  1. A caller passes an absolute path (which `resolve_corpus_path` accepts per `config.py:54-69`). `corpus.get(absolute_path)` returns None — `is_image` is False — control falls through to `extract_frame()` / `sample_frames()`, which will ffprobe a PNG and error out.
  2. The corpus was reorganized today (intent §"Concrete scenarios" #2) but `list_corpus(rescan=True)` hasn't been called yet. A freshly added image is on disk but not in the `items` table; `is_image` is False; ffmpeg path explodes.
- **Why:** Boundary integrity. The tool's classification should be a pure function of the path (which the existing `corpus.classify(Path)` already is — see `corpus.py:25-33`), not coupled to the side-effect of a prior `scan()`. The current design folds `scan()` state into runtime correctness.
- **Suggested defense:** Replace the corpus.get lookup with `is_image = abs_path.suffix.lower() in IMAGE_EXTS` (importing the constant from `corpus.py`), and additionally call `corpus.get()` only once if you still want to surface the indexed metadata.

### F-arch-003 — `detect_objects` `width` parameter has misleading semantics

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/detect.py:128-144, 222`
- **Hunk anchor:** plan.md §"What ships" #4 tool surface lists `width=1280`; docstring at `detect.py:142-143` claims "Frame width for inference. 1280 is YOLO's native".
- **What:** `width` is only ever passed to `extract_frame()` / `sample_frames()` (lines 176, 181) — it controls ffmpeg's source-frame resolution. It is NOT passed to `m.predict(...)` as `imgsz=` (line 222 kwargs only carry `conf`, `iou`, `verbose`, `classes`). YOLO will internally resize whatever it's given to its native 640. Two consequences:
  1. The docstring's claim that 1280 is YOLO's native is wrong — YOLOv8's default is 640. Operator may believe they're controlling inference resolution.
  2. For the `is_image` branch, `width` is silently ignored entirely (YOLO sees the original file, no ffmpeg scale step), yet `width` IS folded into the cache key (`detect.py:159, 161`). Different `width` values against the same image produce cache-key churn for identical outputs.
- **Why:** Contract conformance — the public surface advertises a knob it doesn't actually expose, and the cache-key formulation pretends an irrelevant param matters.
- **Suggested defense:** Either (a) rename to `source_width` and clarify the docstring, plus drop `width` from the cache key on the `is_image` path; or (b) pass `imgsz=width` into the predict kwargs and keep the current name.

### F-arch-004 — `_get_model` in detect.py uses process-global `os.chdir` for download path

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/detect.py:60-93`
- **Hunk anchor:** plan.md §"What ships" #4: "`YOLO_CONFIG_DIR` env in Dockerfile pins weight cache to bind-mount."
- **What:** The first-call download path computes `weight_path` (line 79) — then never uses it. Instead, the function chdirs into `weight_dir`, calls `YOLO(filename)` so ultralytics' relative-path resolution lands the download in cwd, then chdirs back (lines 84-90). The `weight_path` variable is dead. Two architectural issues:
  1. `os.chdir` is process-global. `_get_model` is invoked from inside `loop.run_in_executor` (`detect.py:246`). Two concurrent `detect_objects` calls during cold start can race on cwd: thread A chdir → weight_dir, thread B chdir → weight_dir (no-op), thread A YOLO() (downloading), thread B YOLO() (downloading or partially downloaded), thread A chdir → old_cwd, thread B chdir → old_cwd (now the original — fine), but during the window any _unrelated_ relative path resolution elsewhere in the process is broken.
  2. The presence of an unused `weight_path` reads as a half-finished refactor — the natural cleaner form is `YOLO(str(weight_path))`, which avoids chdir entirely (ultralytics accepts absolute paths).
- **Why:** Boundary integrity (process-global side effect from within a thread-pool worker) + abstraction cost (the chdir dance only exists because the obvious abstraction wasn't taken).
- **Suggested defense:** Replace lines 84-90 with `model = YOLO(str(weight_path))`. Confirms via ultralytics docs / source that absolute-path arg is supported (it is — `YOLO()` calls `attempt_download_asset` which respects an absolute path).

### F-arch-005 — `OllamaClient` constructed per-frame inside vision-mode loop

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/flir.py:292-311` (called from the per-frame loop at 443-450)
- **Hunk anchor:** plan.md §"What ships" #2 — vision-mode HUD extraction.
- **What:** `_vision_extract_frame` instantiates a fresh `OllamaClient(cfg)` and `aclose()`s it for every frame. For a 5-frame sample that's 5 httpx clients spun up and torn down. The HTTP-client object is meant to be reused across calls in a session (httpx connection-pool, keepalive).
- **Why:** Abstraction cost. The natural shape is one client per `flir_hud_ocr` invocation, passed into the per-frame helper. Overshadowed by ollama inference latency in absolute terms, but the abstraction is wrong-side-out.
- **Suggested defense:** Hoist the `OllamaClient(cfg)` instantiation into `flir_hud_ocr`'s vision-mode branch (around `flir.py:443`), pass it into `_vision_extract_frame`, and aclose it once after the frame loop.

### F-arch-006 — Stem-only audio cache path collides across subdirectories

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/audio.py:160`
- **Hunk anchor:** intent §Invariants: "ffmpeg-extracted frames and audio WAVs live under `cache_dir/{frames,audio}/<rel-path-stem>/` and ARE reused across tool calls. The stem-only path means moving the source video to a different directory does NOT invalidate the extracted derivatives."
- **What:** `audio_dir = cfg.cache_dir / "audio" / Path(rel_path).with_suffix("").name`. This mirrors the existing v0.1 pattern in `video.py:135`. Intent declares this is _deliberate_. But: if the corpus contains `Release_1/clip_001.mp4` AND `Release_2/clip_001.mp4` (different files, same basename), both will share `cache/audio/clip_001/16k_mono.wav`. The second call writes its WAV; subsequent transcribes of the first file silently use the wrong audio. Today's Release_1 reorg means this regression class is on the table.
- **Why:** Plan traceability — intent treats the stem-only path as a feature (reorg resilience), but the _flip side_ (basename collisions) isn't acknowledged. Either the assumption "basenames are unique in the corpus" should be stated as an explicit invariant (with a guard), or the cache key should include a path hash to disambiguate.
- **Suggested defense:** Append a short hash of the relative dir to the cache-dir name, e.g. `Path(rel_path).with_suffix("").name + "_" + hashlib.sha256(rel_path.encode()).hexdigest()[:8]`. The cost is a one-time re-extract per file; the benefit is collision-proofing under the kind of reorg that just happened.

### F-arch-007 — Unused imports + version drift in `__init__.py`

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/flir.py:21, 25` and `src/uap_analyzer/__init__.py:3`
- **Hunk anchor:** pyproject.toml diff bumps version `0.1.0 → 0.4.0` (line 4).
- **What:** Two cosmetic cleanups:
  1. `flir.py:21` imports `hashlib` and `flir.py:25` imports `Path`; neither is used in the module (the file has 485 lines and no `hashlib.` or `Path(` call).
  2. `src/uap_analyzer/__init__.py:3` still has `__version__ = "0.1.0"` while `pyproject.toml` is `0.4.0`. Healthz doesn't surface `__version__` so this is purely cosmetic, but every release commit advertised a version bump.
- **Why:** Plan traceability + suggestion-level hygiene. Not load-bearing, but the version drift is the kind of artifact that bites later (someone reads `__version__` from a smoke test and gets wrong answer).
- **Suggested defense:** Remove the unused imports from `flir.py`; bump `__version__` in `__init__.py` to match `pyproject.toml`. Or have it read from `importlib.metadata.version("uap-analyzer")` and stop hand-syncing.

### F-arch-008 — Healthz does not surface `whisper_model` or YOLO state

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/__main__.py:36-50`
- **Hunk anchor:** intent §"Behaviors under review" #6: "response includes `vision_model`, `hud_model`, `text_model`, `corpus_items`."
- **What:** The intent enumerates the four fields healthz must expose. `hud_model` was added in this diff (line 46) ✅. But `whisper_model` and `whisper_compute_type` are NOT exposed, despite being equally operator-facing config controls (and being highlighted as plan items 3.). The asymmetry is awkward — the operator can probe healthz to see which vision/text/HUD models are bound, but has to read `.env` to know the whisper config.
- **Why:** Contract conformance is borderline (intent only lists the four named fields), but the _spirit_ of healthz-as-config-mirror is broken by the omission. Adversary stage will reasonably ask "why is whisper config invisible at runtime?"
- **Suggested defense:** Add `"whisper_model": cfg.whisper_model, "whisper_compute_type": cfg.whisper_compute_type` to the healthz dict.

### F-arch-009 — Plan-untraceable hunk: `src/uap_analyzer/__init__.py` version drift

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/__init__.py:3`
- **What:** Each release commit (v0.2.0, v0.2.1, v0.3.0, v0.4.0) advertises a version bump, but `__init__.py:__version__` is never touched in the diff range. It still reads `"0.1.0"`. This isn't a missing hunk — it's a hunk that _should_ have existed and didn't. Logged separately from F-arch-007 because the lens here is plan traceability: every "What ships" item in plan.md announces a version, but the in-process version string was never reconciled.
- **Why:** Plan traceability — the plan declares four releases; the only place a runtime can introspect the release stamp is via `pyproject.toml`-derived metadata (which isn't read anywhere in the package).
- **Suggested defense:** Resolve via F-arch-007's `importlib.metadata` suggestion.

## Cross-Reviewer Ready Notes

- **For security reviewer:**
  - `flir.py:316-329`: vision-mode JSON parsing trusts the ollama daemon's response. The `_normalize_vision_fields` (`flir.py:251-289`) is defensive against bad types/values, but a deliberately crafted JSON blob with extremely long strings could inflate the cached result (no length cap on `raw_text` beyond the `[:240]` slice — but the model could fabricate any of the typed fields). Worth a look at result-size bounds.
  - `detect.py:84-90`: process-global `os.chdir` invoked from inside an executor thread (see F-arch-004) — under concurrent load, an unrelated request that relies on relative-path resolution could see the wrong cwd for the duration of the YOLO weight-download window. Cold-start-only, but worth flagging.
  - `deploy/zaphod-deploy.sh:54`: `curl -fsS "http://${HOST#*@}:3260/healthz" | python3 -m json.tool` — no TLS, plaintext over LAN. The intent explicitly accepts this (trusted LAN), but worth verifying no path mounts the script on a network where that posture breaks.
  - `ollama_client.py` diff: `json_mode=True` is opt-in per call site, but the schema returned by the model is trusted blindly downstream. Defensive normalisation in `_normalize_vision_fields` does the right thing; sec lens may want to confirm the same posture holds for any future tools that bolt onto the same client.

- **For performance reviewer:**
  - `flir.py:292-311` (F-arch-005): per-frame httpx-client construction. Likely amortised by ollama latency but worth quantifying.
  - `detect.py:60-93` (F-arch-004): `os.chdir` during cold-start model download — concurrency hazard, but also a wall-clock observation: the chdir + YOLO() + chdir-back is on the critical path of the first call. Switching to `YOLO(str(weight_path))` removes a syscall round-trip too.
  - `audio.py:160` and `video.py:135` use `Path(rel_path).with_suffix("").name` for the cache dir — basename collisions (F-arch-006) are the architectural concern; the perf angle is that an un-detected collision will trigger an unnecessary ffmpeg re-extract every time the "wrong" file gets touched.
  - Module-level model caches (`audio.py:43`, `detect.py:40`) have no eviction policy. Long-running container + operator switching `WHISPER_MODEL` per call will accumulate every model variant in RAM (`medium.en` ~770MB, large ~1.5GB). Probably fine for the current single-operator workflow; flag for perf lens if multi-tenant ever becomes a thing.
