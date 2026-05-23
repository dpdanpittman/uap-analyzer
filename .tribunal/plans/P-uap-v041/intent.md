# Intent — uap-analyzer v0.4.1 patch (tribunal-driven fix pass)

## System Identity

- **Plan ID:** P-uap-v041
- **Scope:** the single commit `7f06cb6` ("v0.4.1: tribunal review findings — chdir race, cache-key correctness, LAN-DOS bounds, deploy secret excludes"). Net diff: ~1443 insertions across 14 source files. Diff basis: `c267b63..HEAD`.
- **Purpose:** **verify-the-fix**. This is not a fresh greenfield review — it's a focused audit of whether the v0.4.1 patches actually close each of the 12 must-fix items surfaced by P-uap-v04, without regressing the v0.4.0 surface or introducing new defects. The companion P-uap-v04 reports are on disk at `.tribunal/reports/P-uap-v04/` and are the spec this patch was built against.

## Behaviors under review

Each item below is a P-uap-v04 finding the v0.4.1 patch claims to close. The audit must confirm closure OR contest it.

1. **🔴 chdir race in `detect.py:_get_model`** (was perf-F-perf-001 Critical, cross-confirmed by arch-F-arch-004 + sec-F-sec-003).
   - **Claim:** v0.4.1 passes an absolute `weight_path` to `YOLO()` instead of `os.chdir`-ing. Concurrent first-calls are serialized via a new `threading.Lock` around `_MODEL_CACHE`.
   - **What the auditor must confirm:** `os.chdir` is gone from `tools/detect.py`. The lock covers both the `_MODEL_CACHE` lookup AND the `YOLO()` load (concurrent cold-starts can't duplicate the load). Lock release on exception paths.

2. **`transcribe_audio` cache key omits `compute_type`** (was arch-F-arch-001).
   - **Claim:** v0.4.1 hashes `compute_type` into the cache key.
   - **Confirm:** `compute_type` is now part of the cache-key tuple in `audio.py:transcribe_audio`. Operator toggling `WHISPER_COMPUTE_TYPE` between deploys gets a fresh cache miss + correct re-run.

3. **`is_image` classification in `detect.py`** (was arch-F-arch-002).
   - **Claim:** v0.4.1 introduces `IMAGE_EXTS` frozenset; classification is `abs_path.suffix.lower() in IMAGE_EXTS`.
   - **Confirm:** No `corpus.get(rel_path)` dependency for the classification. Works for absolute paths, never-scanned files, and the standard image extensions (.png, .jpg, .jpeg, .webp, .bmp, .gif). Doesn't break video-path classification.

4. **`width` parameter in `detect.py`** (was arch-F-arch-003).
   - **Claim:** v0.4.1 passes `imgsz=width` to `model.predict()` and corrects the docstring (YOLO native is 640, not 1280).
   - **Confirm:** `imgsz=width` is in the predict kwargs. Cache-key fold-in is correct. Docstring matches behavior.

5. **Unbounded `_MODEL_CACHE`** (was perf-F-perf-002).
   - **Claim:** v0.4.1 caps both audio + detect caches at 3 entries via `OrderedDict` + `move_to_end` + `popitem(last=False)`.
   - **Confirm:** Both `audio.py` and `detect.py` have the LRU pattern. The cap is enforced inside the lock so eviction races don't leak entries.

6. **`_MODEL_CACHE` not thread-safe** (was perf-F-perf-003).
   - **Claim:** v0.4.1 wraps cache access in `threading.Lock`.
   - **Confirm:** Same lock as (1). The lock is held across the load, not just the lookup.

7. **Bare-int httpx timeout** (was perf-F-perf-005).
   - **Claim:** v0.4.1 splits into `httpx.Timeout(connect=5, read=cfg.ollama_timeout, write=10, pool=10)`.
   - **Confirm:** `ollama_client.py:OllamaClient.__init__` uses the structured `httpx.Timeout`. A downed ollama daemon would now fail fast on connect (5s) instead of hanging for `ollama_timeout` seconds.

8. **LAN-DOS via unbounded `sample_count`/`width`/`beam_size`** (was sec-F-sec-001).
   - **Claim:** v0.4.1 introduces a `_bounded()` helper in `server.py`. Caps: `sample_count<=100`, `width<=4096`, `beam_size<=20`, `max_seconds<=86400`, `at_seconds<=86400`, `classes<=80 entries`, `initial_prompt<=1024 chars`.
   - **Confirm:** Every MCP `@mcp.tool()` that accepts these args calls `_bounded()` before dispatching to the underlying tool. Bounds rejection happens BEFORE any I/O or model load. No tool surface bypasses the helper.

9. **`vision_model` arg lacks whitelist** (was sec-F-sec-002).
   - **Claim:** v0.4.1 adds `VALID_HUD_MODELS` frozenset in `flir.py`; unknown values raise `ValueError` before any cache or model touch.
   - **Confirm:** The whitelist exists, default `qwen2.5vl:7b` is in it. Validation happens BEFORE `cfg.resolve_corpus_path()` or cache lookup. Sibling tools' patterns (`VALID_MODELS` in audio + detect) are preserved.

10. **Cache-key `|`-concat collision spoofing** (was sec-F-sec-005).
    - **Claim:** v0.4.1 replaces concatenation with sha256[:16] of the joined tuple, applied uniformly across `audio.py`, `detect.py`, `flir.py`.
    - **Confirm:** Each tool's cache key is hashed. Hash inputs include EVERY param that materially affects output (model, language, vad_filter, beam_size, prompt-hash, initial_prompt presence, compute_type for whisper; model, confidence, iou, width, classes, is_image for detect; mode, sample_count, width, region_key, model_key for flir). No raw client-controlled strings remain in the key.

11. **Deploy script `--exclude=.env` too narrow** (was sec-F-sec-008).
    - **Claim:** v0.4.1 broadens the exclude set to .env*, .envrc, secrets/, .secrets/, *.pem, _.key, _.crt, _.p12, _.pfx, _.gpg, _.asc, id_rsa, id_ed25519. Adds shape validation for `ZAPHOD_REMOTE_DIR` (must be absolute, no traversal segments).
    - **Confirm:** The rsync invocation includes the broader exclude set. The validation runs early in the script (before any rsync). Both checks (absolute + no '..') are present.

12. **Hygiene bundle** (was arch-F-arch-007/008/009 + sec-F-sec-006/010).
    - **Claims:**
      - `__init__.py:__version__` bumped to `0.4.1` (was `0.1.0`).
      - Healthz exposes `whisper_model` + `whisper_compute_type`.
      - `bool` rejected in `_normalize_vision_fields` numeric paths (was accepting True→1.0, False→0.0).
      - `RecursionError` caught around `json.loads` in vision mode + 64KB length clamp.
    - **Confirm:** Each of those is present in the patched code.

## Invariants under audit

- **No regression on v0.4.0 surface.** The seven v0.1 tools (`list_corpus`, `analyze_video`, `extract_frame`, `describe_image`, `analyze_pdf`, `search_corpus`, `index_corpus`) plus the three v0.2-v0.4 tools (`flir_hud_ocr`, `transcribe_audio`, `detect_objects`) must all still load. The container redeployed and healthz returns 220 corpus_items — that's the smoke-test floor. The audit should look for behavioral regressions the smoke missed.
- **No new abstractions introduced beyond what the findings required.** v0.4.1 is a bug-fix patch, not a refactor. Any abstraction (a new helper, a new module, a new env var) that doesn't trace back to a P-uap-v04 finding should be flagged as scope creep.
- **No new prompt-injection surfaces or trust-boundary changes.** v0.4.1 didn't change `cfg.resolve_corpus_path()`, didn't change the MCP auth posture, didn't change the ollama trust model. Confirm.

## Failure modes to look for (regressions)

- The `threading.Lock` in `_get_model` correctly serializes cold-starts, but does it deadlock with the asyncio event loop? (The load runs inside `run_in_executor` so it's a worker-thread context — Python `threading.Lock` is fine there. But verify.)
- The `_bounded()` helper raises ValueError BEFORE the tool dispatches. Does the MCP layer surface that as a tool error properly, or does it 500 the request? (Not in scope to audit MCP plumbing, but flag if you see something weird.)
- The hashed cache keys mean older cache entries (keyed by the v1/v2 pipe-concat format) become orphaned. Was that acceptable? (Yes — the cache is a derivative; cache misses re-populate.)
- The whisper LRU is module-global. Under uvicorn's multi-worker setup, would each worker have its own cache? (uap-analyzer runs single-process so this isn't a problem today; flag for future.)
- The `imgsz=width` change in detect.py — does that actually do what the docstring claims? Or does ultralytics ignore non-multiple-of-32 widths and the user thinks 1280 is being used when it's silently being 1248?

## Non-goals

- Not auditing the underlying inference correctness (we don't run ollama/whisper/yolo in the audit).
- Not re-auditing the v0.1 base surface; out of scope for this patch.
- Not auditing the 5 deferred items (perf-F-perf-006 through -010, sec-F-sec-004, -F-sec-009) — those were explicitly punted to v0.4.2+.
- Not running the deploy script against a real zaphod — code-only review.

## Trust boundaries

Unchanged from P-uap-v04. The patch tightens the existing surface, doesn't expand it. The audit must confirm "tightens" rather than "rearranges + introduces a new gap."

## Performance bounds

The new bounds (sample_count<=100, etc.) are 1-2 orders of magnitude above realistic use. Confirm that's true for the caps as written — if any cap is below the documented v0.4.0 performance envelope, that's a Warning.

## Concrete scenarios

1. **Concurrent first-call to detect_objects with two different models.** Pre-patch: race on cwd + race on cache. Post-patch: lock serializes both, second caller gets a cache miss but no corruption. Confirm code path.

2. **Operator toggles `WHISPER_COMPUTE_TYPE=int8 → float16` between deploys.** Pre-patch: stale results from int8 cache served as if they were float16. Post-patch: fresh cache key, fresh inference. Confirm code path.

3. **Hostile client calls `flir_hud_ocr(mode="vision", vision_model="evil:1b")`.** Pre-patch: silent acceptance, cache namespace bloat, ollama load. Post-patch: rejected at the entry with `ValueError("unknown vision_model ...")`. Confirm.

4. **Client calls `transcribe_audio(beam_size=999999)`.** Pre-patch: faster-whisper attempts a beam-search of that width, OOMs or hangs. Post-patch: `_bounded()` raises before any tool call. Confirm.

5. **`zaphod-deploy.sh` runs on a host with `~/src/uap-analyzer/secrets/api.pem` present.** Pre-patch: rsync `--delete` wipes the file. Post-patch: `*.pem` is in the exclude set, file survives. Confirm.

The audit's verdict is binary for each item: **closed** (the patch did what it claimed) or **not closed** (the patch is incomplete or wrong). If all 12 close cleanly, the trio should approve and adversary stage gets the gate for the first time on uap-analyzer.

## Reading order

1. `.tribunal/reports/P-uap-v04/consolidated-verdict.md` — the spec this patch was built against.
2. `git diff c267b63..HEAD` — the patch itself.
3. The per-lens P-uap-v04 reports for the original-context understanding of why each finding was raised.
4. Cross-check: for each finding, point at the line in the v0.4.1 patch that addresses it.
