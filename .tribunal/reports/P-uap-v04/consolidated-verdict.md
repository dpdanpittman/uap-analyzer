# Tribunal Hybrid Review — Consolidated Verdict · P-uap-v04

**Date:** 2026-05-22
**Diff:** `0ce846d..c267b63` (uap-analyzer v0.2.0 → v0.4.0 + site refresh)
**Discovery:** clawpatch heuristic mapper, 6 features
**Stage 1 reviewers:** `tribunal-reviewer-arch`, `tribunal-reviewer-sec`, `tribunal-reviewer-perf` (lens-parallel, dispatched in one message)
**Stage 2 (adversary):** **not dispatched** — per methodology, adversary only runs on consolidated Approve.

## Consolidated verdict: **Request Changes**

| Lens         | Verdict             | Critical | Warning | Suggestion |
| ------------ | ------------------- | -------- | ------- | ---------- |
| Architecture | Request Changes     | 0        | 4       | 5          |
| Security     | Request Changes     | 0        | 6       | 4          |
| Performance  | Request Changes     | 1        | 4       | 5          |
| **Total**    | **Request Changes** | **1**    | **14**  | **14**     |

## Cross-confirmed findings (highest signal)

When two or more lenses independently surface the same defect, that's the strongest signal Tribunal can produce. Three items in this review:

### 🔴 `_get_model` chdir race in `tools/detect.py:60-93`

- **perf F-perf-001** (Critical) — Race condition under concurrent first-calls. `os.chdir(weight_dir)` runs inside a default `ThreadPoolExecutor` task via `loop.run_in_executor`; concurrent first-calls can corrupt the YOLO weight download, and the cwd mutation leaks to any other thread doing relative-path resolution during the load window. The `_MODEL_CACHE` check is not atomic — both threads will build the model.
- **arch F-arch-004** (Warning) — Same finding from the architecture lens: process-global cwd from within `run_in_executor`. `weight_path` is computed but unused — `YOLO(str(weight_path))` would remove the chdir dance entirely.
- **sec F-sec-003** (Warning) — Same finding from the security lens: `os.chdir` mutates process-global cwd from a worker thread. Not exploitable today (sandbox prefix is absolute) but defense-in-depth gap.

**Three lenses, one fix:** pass an absolute weight path to `YOLO()` and wrap `_MODEL_CACHE` access in a `threading.Lock`. Eliminates the race + the cwd-leak + the duplicate-load.

### 🟡 Per-frame OllamaClient build in vision mode

- **perf F-perf-004** (Warning) — `flir.py:292-311` builds a fresh `OllamaClient` (and underlying `httpx.AsyncClient`) per frame; loses TCP keepalive across frames in a 5-frame sweep.
- **arch F-arch-005** (Suggestion) — Same finding from architecture: hoist to one client per `flir_hud_ocr` invocation.

### 🟡 Stem-only cache path collision

- **arch F-arch-006** (Suggestion) — `audio.py:160` uses `Path(rel_path).with_suffix("").name` so two videos with the same basename in different dirs (e.g. `Release_1/DOD_X.mp4` and `Release_2/DOD_X.mp4`) collide on the WAV cache path.
- **sec F-sec-004** (Warning) — Same finding from security; flagged as the security trade-off behind the design intent documented in intent.md §Invariants.

## Must-fix shortlist (the Critical + Warnings that block this)

In rough priority order — the goal is `Request Changes → Approve` on a re-review:

1. **🔴 Fix the chdir race in `detect.py:_get_model`** (cross-confirmed, see above).

2. **`transcribe_audio` cache key omits `compute_type`** (arch F-arch-001) — the module-level `_MODEL_CACHE` is keyed by `(model_name, compute_type)` but the cache key in `corpus.put_cached` is only keyed by `model_name`. Operator toggling `WHISPER_COMPUTE_TYPE` between deploys returns stale results from the wrong-quantization model. Correctness bug, trivial fix.

3. **`is_image` decision in `detect.py:169-172` is fragile** (arch F-arch-002) — Uses `corpus.get(rel_path)` which couples correctness to scan state AND to exact-string path match. Absolute paths (which `resolve_corpus_path` accepts) silently fall through to the ffmpeg branch on PNG inputs. Should classify by extension via the existing `corpus.IMAGE_EXTS`.

4. **`detect.py` `width` parameter is misleading** (arch F-arch-003) — Only controls source ffmpeg scale; never passed as `imgsz=` to YOLO; ignored entirely on image inputs; yet folded into cache key. Docstring claim "1280 is YOLO's native" is wrong (YOLO native is 640). Either pass `imgsz=width` to `model.predict()` or drop the param + cache-key entry.

5. **Unbounded `_MODEL_CACHE` (audio + detect)** (perf F-perf-002) — Client-supplied model names. A/B sweep through Whisper variants resident ~3GB; YOLO variants ~300MB. No LRU, no metric, no eviction. Add an upper bound (3–5 entries) with LRU eviction.

6. **`_MODEL_CACHE` not thread-safe** (perf F-perf-003) — Concurrent first-calls duplicate the load (resolved together with #1's lock).

7. **Bare-int httpx timeout** (perf F-perf-005) — `ollama_client.py:29` passes `cfg.ollama_timeout` (300s) as a bare int to `httpx.AsyncClient(timeout=...)`, applied uniformly to connect/read/write/pool. Downed ollama daemon → 5-min hang per call, × N frames for vision-mode FLIR = up to 25 min. Fix: `httpx.Timeout(connect=5.0, read=cfg.ollama_timeout, write=10.0, pool=10.0)`.

8. **LAN-side DOS via unbounded sample_count / width / beam_size** (sec F-sec-001) — `flir_hud_ocr(mode="vision", sample_count=10000)` burns ~4h of CPU per call. Add upper bounds to the MCP tool signatures (e.g. `sample_count` ≤ 100, `width` ≤ 4096).

9. **`vision_model` arg lacks whitelist** (sec F-sec-002) — Only the new client-controlled model param without validation; the other model args (`whisper` model, `yolo` model) are whitelisted via `VALID_MODELS`. Also feeds the cache key → cache bloat.

10. **Cache-key `|`-separator collision spoofing** (sec F-sec-005) — Client-controlled tokens (`language` in audio, `vision_model` in flir) concatenated raw with `|` enable key-collision spoofing across distinct (model, language) tuples. Hash the tuple or escape the separator.

11. **Deploy script `--exclude=.env` too narrow** (sec F-sec-008) — `deploy/zaphod-deploy.sh` only protects `.env`; other secret patterns (`*.pem`, `.envrc`, `secrets/`) get wiped by `--delete`. Also: `ZAPHOD_REMOTE_DIR` is unvalidated — a malicious value could rsync to an arbitrary remote path.

12. **`vision_model` cache key bloat + missing wider rate limiting** (sec F-sec-002 / F-sec-005 — see above).

## Suggestions (non-blocking but worth grouping into a v0.4.1 patch)

### Code hygiene

- **arch F-arch-007** — Unused imports in `flir.py:21` (`hashlib`), `flir.py:25` (`Path`).
- **arch F-arch-009** — `src/uap_analyzer/__init__.py` `__version__` still `"0.1.0"` while pyproject is `0.4.0` — no release commit touched it.
- **arch F-arch-008** — Healthz exposes `hud_model` but not `whisper_model` / `whisper_compute_type`. Add for symmetry.

### Hardening

- **sec F-sec-006** — `json.loads` in vision-mode doesn't catch `RecursionError`; content not length-clamped before parse.
- **sec F-sec-007** — `int(max_seconds)` truncation in audio cache key drops fractional seconds.
- **sec F-sec-009** — `/mcp` and `/healthz` remain unauthenticated. Out-of-scope per intent.md but the new tools meaningfully amplify the cost of a LAN-trust violation.
- **sec F-sec-010** — `isinstance(x, (int, float))` in `_normalize_vision_fields` accepts `bool` (True/False become 1/0). Add `not isinstance(x, bool)` guard.
- **perf F-perf-006** — Sequential vision loop. Could `asyncio.gather` per frame for ~3-4× wall-clock speedup; bounded by ollama's concurrency budget.
- **perf F-perf-007** — Cache-row bloat for long transcripts: a 90-min press conference stores 100KB+ in a single row, queried on every cache lookup.
- **perf F-perf-008** — Redundant per-region preprocess in `flir.py`: greyscale + autocontrast + 2× upscale happens 6 times per frame; could do once and crop the preprocessed image.
- **perf F-perf-009** — Per-frame ffmpeg subprocess spawn in `extract_frame`. For N-frame sampling, one ffmpeg invocation with `select=` filter would be 3-5× faster.
- **perf F-perf-010** — Observability gap during model downloads (whisper + yolo). User sees nothing for 30-90s on first call; log a progress line.

## Methodology positives (what the reviewers explicitly confirmed)

Worth recording so the patch doesn't regress what's already working:

- All three new tool modules import heavy deps (torch/ultralytics, faster-whisper, pytesseract, PIL, ollama_client) **lazily inside functions** — server boot stays light. ✅
- All three new tools route every user-supplied path through `cfg.resolve_corpus_path()` and persist results via `corpus.put_cached`. ✅
- The `@mcp.tool()` decorator boundary in `server.py:287-432` is clean — no inference imports leak into the registration path. ✅
- Vision-mode JSON parsing is defensively normalized: `_normalize_vision_fields` enforces type + range bounds on every field; markdown-fenced JSON is tolerated; parse failures log + return empty rather than crash. ✅
- Subprocess calls use `create_subprocess_exec` with arg lists (no shell injection). ✅
- ffmpeg invocations don't pass user input as flags. ✅

## Next actions for the PM

1. Triage the must-fix shortlist — accept, dismiss with rationale, or reclassify any item. Default disposition: all 12 stay as required.
2. Open a `P-uap-v041` plan to land the must-fix patches + chosen suggestions.
3. Re-run the lens trio after the patch lands. If clean, adversary stage gets the gate.

## Full lens reports

- Architecture: `.tribunal/reports/P-uap-v04/arch-report.md`
- Security: `.tribunal/reports/P-uap-v04/sec-report.md`
- Performance: `.tribunal/reports/P-uap-v04/perf-report.md`
