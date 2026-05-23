# Tribunal Lens Review — Security · P-uap-v04

**Reviewer:** tribunal-reviewer-sec
**Verdict:** Request Changes

## Summary

Three new MCP tools (`flir_hud_ocr` with ocr+vision modes, `transcribe_audio`, `detect_objects`) materially expand the inference attack surface on a no-auth LAN-trusted server. Path sandboxing via `cfg.resolve_corpus_path()` is correctly applied at every user-supplied path entry point, and subprocess invocations route through `asyncio.create_subprocess_exec` (no shell). However, several inputs reach inference/I/O paths without bounds (`sample_count`, `width`, `vision_model`), the `os.chdir`-in-thread pattern in `detect.py` mutates process-global state from a worker thread, cache keys use `|`-separator concatenation that lets a client-controlled `vision_model` collide with other keys, and the on-disk cache directory naming uses path stems only which lets two corpus files with the same stem share frame/audio caches. The deploy script preserves only `.env` and unconditionally `--delete`s the rest, which is correct for the documented incident but does not protect other operator-side files that might land alongside it. None of these are exploitable from outside the LAN today; the LAN-trust premise is what holds the line.

## Findings

### F-sec-001 — Unbounded `sample_count` / `width` on inference-heavy tools enables LAN-side DOS

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/detect.py:117-164`; `src/uap_analyzer/tools/flir.py:340-414`; `src/uap_analyzer/tools/audio.py:99-138`
- **Attack scenario:** Any client on the LAN (no auth on `/mcp`) calls `flir_hud_ocr(mode="vision", sample_count=10000)`. The validator block at `flir.py:376-387` checks `mode` and region names but never bounds `sample_count`. The call then loops over 10000 frames, each triggering an ollama vision inference (~1.5s) — ~4 hours of CPU pegging per request. Same shape applies to `detect_objects(sample_count=10000)` and to `width=100000` (memory blow-up in ffmpeg → PIL → model). `transcribe_audio` doesn't sample frames but accepts unbounded `beam_size` and `max_seconds=None` (full-file inference with no cap).
- **Trust boundary crossed:** MCP client → server → ollama / faster-whisper / yolo (host-resource consumption).
- **Suggested defense:** Cap `sample_count` (e.g. ≤ 64), `width` (e.g. ≤ 4096), and `beam_size` (e.g. ≤ 10) with pre-I/O validation that mirrors the `VALID_MODELS` whitelist style already used elsewhere.

### F-sec-002 — `vision_model` is client-controlled with no whitelist and is concatenated into the cache key

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/flir.py:350,390,446`; `src/uap_analyzer/server.py:294-333`
- **Attack scenario:** Client passes arbitrary `vision_model` string. (a) Unlike `transcribe_audio`'s `model` (whitelisted against `VALID_MODELS` at `audio.py:131-134`) and `detect_objects`'s `model` (whitelisted at `detect.py:149-150`), `flir_hud_ocr`'s `vision_model` has no validation before being sent to ollama at `flir.py:307`. A malicious or buggy client can name any model, forcing ollama to attempt to load whatever is named (cost / failure mode varies by daemon config). (b) The same untrusted value is used to construct the cache key via `model_key = (vision_model or cfg.ollama_hud_model).replace(":", "_")` at `flir.py:390`. An attacker spraying unique `vision_model` values inflates `analysis_cache` rows in SQLite (each entry contains a full result JSON), bloating the bind-mounted cache.
- **Trust boundary crossed:** MCP client → cache (storage) + ollama (compute).
- **Suggested defense:** Validate `vision_model` against an allowlist (e.g. `("qwen2.5vl:7b", "llama3.2-vision:11b")`) before any I/O, mirroring the whisper/yolo pattern.

### F-sec-003 — `os.chdir` inside `_run_detect` thread-pool worker mutates process-global cwd

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/detect.py:84-90` (called from `_run_detect` at `detect.py:189-244`, which itself runs inside `loop.run_in_executor(None, _run_detect)` at `detect.py:246`)
- **Attack scenario:** `_get_model` does `os.chdir(weight_dir)` / `os.chdir(old_cwd)` inside the executor thread. `os.chdir` is process-global, not thread-local. If two `detect_objects` calls land concurrently against two different models (cold cache for both), or if any other concurrent request reads cwd-relative state during the chdir window, behaviour is non-deterministic and could land file writes in the wrong directory. `cfg.resolve_corpus_path()` happens to be safe because `cfg.data_dir` is pre-resolved absolute, so the sandbox is not broken — but any future code that does `Path(rel).resolve()` without an absolute prefix would silently bypass the sandbox during a chdir window.
- **Trust boundary crossed:** intra-process correctness; not externally exploitable today, but a defense-in-depth gap.
- **Suggested defense:** Replace the `os.chdir` pattern with ultralytics' supported `cwd=` / `weights=` / `YOLO(weights_path)` form, or wrap the `_get_model` first-call download in a process-wide lock outside the executor.

### F-sec-004 — Cache directory naming uses `Path(rel_path).with_suffix("").name`, collapsing distinct corpus files with shared stems

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/audio.py:160`; `src/uap_analyzer/tools/video.py:135`
- **Attack scenario:** Two corpus files `Release_1/DOD_1.mp4` and `Release_2/DOD_1.mp4` both resolve to `cache_dir/audio/DOD_1/16k_mono.wav` and `cache_dir/frames/DOD_1/*.jpg`. The first call extracts and persists derivatives keyed by stem; the second call sees the file exists (`if not wav_path.exists():` at `audio.py:163`) and reuses it. The analysis-cache SQL key DOES include the full rel_path (so the cached transcript / detection JSON is correctly scoped), but the on-disk audio file the cached result was originally derived from belongs to a different source. If an adversary can plant a same-stem file in the corpus (e.g. via the read-only data dir being populated by an upstream pipeline), they can poison frame/audio derivatives that subsequent analysis steps consume. The intent doc explicitly calls out the stem-only behaviour as desirable for cross-directory reuse; this finding flags the security trade-off.
- **Trust boundary crossed:** corpus-curator → tools that read cached derivatives.
- **Suggested defense:** Use a hash of the full `rel_path` (or `Path(rel_path).as_posix().replace("/", "_")`) as the directory key instead of `.name`. Persists the cross-directory reuse property only if the source path actually matches.

### F-sec-005 — Cache-key string uses `|` as separator and admits client-controlled tokens

- **Severity:** Warning
- **Location:** `src/uap_analyzer/tools/audio.py:144-154` (`language`, `initial_prompt`); `src/uap_analyzer/tools/flir.py:389-394` (`model_key`, `region_key`)
- **Attack scenario:** Audio cache key is `"v1|m{model}|l{language or 'auto'}|..."`. The `language` arg is not validated against an ISO whitelist before string interpolation. A client passing `language="en|b5|v1|max0|noinit"` yields a key that collides with the canonical key for `(model, en, beam_size=5, vad=True, max_seconds=0, no initial_prompt)`. The cached result for one parameter set is then served for a different one. Similar shape in `flir.py:390` where `vision_model.replace(":", "_")` only neutralizes one separator out of many. `transcribe_audio`'s `initial_prompt` is sha256'd (good) but `language` is not.
- **Trust boundary crossed:** MCP client → cached-result integrity.
- **Suggested defense:** Either sha256 the full param tuple into a single hex digest (matching the `initial_prompt` pattern already in `audio.py:152`) or validate `language` and `vision_model` against an allowlist before they reach the key builder.

### F-sec-006 — Vision-mode JSON parse does not bound nesting depth; `RecursionError` is uncaught

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/flir.py:316-329`
- **Attack scenario:** `_json.loads(content)` is wrapped in `except (ValueError, TypeError)`. A pathologically nested JSON payload (`[[[[...]]]]`) raises `RecursionError`, which is not caught — the per-frame call fails up to the broader `except Exception` in `flir_hud_ocr` at `flir.py:447`, which is fine BUT (a) the content is bounded only by ollama's `max_tokens=512` (~512 tokens ≈ 2-4KB of JSON), making a successful crafted payload tight but not impossible if a future caller raises max_tokens, and (b) any non-ValueError/TypeError exception inside the parse leaves `parsed = {}` only because of the outer wrap. If `_normalize_vision_fields` ever gains a code path that depends on the original `parsed` shape, this is fragile.
- **Trust boundary crossed:** ollama model (untrusted output) → server.
- **Suggested defense:** Broaden the catch to `Exception` (with a logged warning) since this is a non-critical defensive parse, OR clamp `content` length explicitly (e.g. `content = content[:16384]`) before `_json.loads`.

### F-sec-007 — `transcribe_audio` cache key truncates `max_seconds` to int

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/audio.py:151`
- **Attack scenario:** `f"max{int(max_seconds) if max_seconds else 0}"` — `max_seconds=10.4` and `max_seconds=10.9` both render `max10`, so they collide in cache. Whisper's `clip_timestamps=[0.0, float(max_seconds)]` does honor the fractional second, but the cache returns the result for whichever value was computed first. Not exploitable for elevation; correctness suggestion only.
- **Trust boundary crossed:** none meaningful.
- **Suggested defense:** Use `round(max_seconds, 2)` or include the fractional in the key.

### F-sec-008 — Deploy script preserves only `.env`; other host-side files outside the source tree are wiped

- **Severity:** Warning
- **Location:** `deploy/zaphod-deploy.sh:36-44`
- **Attack scenario:** The script `rsync -aP --delete --exclude=.env ...`. The `.env` incident motivated the fix, but the exclude list is narrow: any other operator-curated file in `REMOTE_DIR` that isn't tracked in the local tree (e.g. `secrets.json`, `*.pem`, a `notes.md`, a `.envrc`, a sibling `.env.production`) is silently deleted on every deploy. A future operator landing creds in any non-`.env` filename gets bitten again. Also: `ZAPHOD_REMOTE_DIR` is env-var-controlled with no validation — setting it to `/home/zaphod-beeblebox` instead of `/home/zaphod-beeblebox/src/uap-analyzer` would `--delete` everything in the parent dir (minus the excludes). Operator-trust, but a near-miss.
- **Trust boundary crossed:** deploy-host filesystem.
- **Suggested defense:** Expand the exclude list to cover the common secret-file patterns (`*.env*`, `*.pem`, `*.key`, `secrets/`, `.envrc`) and assert that `REMOTE_DIR` ends in the expected basename before invoking rsync.

### F-sec-009 — `/mcp` and `/healthz` remain unauthenticated; new tools amplify the consequence

- **Severity:** Suggestion (acknowledged as out-of-scope in intent.md but worth restating)
- **Location:** `src/uap_analyzer/server.py:29-35`; `src/uap_analyzer/__main__.py:37-50`
- **Attack scenario:** `mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)` plus `host=0.0.0.0` plus no token/auth means any LAN host (or anything that gets routed into the LAN) can invoke `transcribe_audio`, `flir_hud_ocr(mode="vision")`, and `detect_objects` — each of which has meaningful CPU/inference cost (per F-sec-001). Healthz also discloses ollama_host, model names, and corpus_items count — the latter is a minor disclosure (corpus presence) but the former three are deployment fingerprint info. v0.1 made the LAN-trust call; v0.4 increases what that trust costs if it ever holds wrong.
- **Trust boundary crossed:** LAN perimeter.
- **Suggested defense:** At minimum, gate `/mcp` behind a shared-secret header pulled from `.env` (`UAP_API_TOKEN`) and reject anonymous requests. Out of scope for this diff per intent.md, but the cost/benefit moved.

### F-sec-010 — `isinstance(x, (int, float))` accepts `bool` in `_normalize_vision_fields`

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/flir.py:274,278,282`
- **Attack scenario:** `bool` is a subclass of `int`. If the vision model returns `"range_nm": true`, the check `isinstance(rng, (int, float)) and 0 < rng < 1000` passes (True == 1), and `out["range_nm"] = 1.0` is recorded. Same for `bearing_deg` (True/False → 0/1) and `elevation_deg`. Result is mildly misleading downstream; not exploitable.
- **Trust boundary crossed:** ollama output → consensus aggregation.
- **Suggested defense:** Add `not isinstance(rng, bool) and` to each numeric check, or use `type(rng) in (int, float)`.

## Cross-Reviewer Ready Notes

- **For architecture reviewer:** F-sec-003 (`os.chdir` in executor thread) is at least as much an architecture smell as a security one — the chdir pattern leaks process state out of the tool boundary. Worth confirming whether the model-load path can be moved to a lifespan hook so it never runs concurrently with request handling. Also: F-sec-004 (stem-collision in `cache_dir/{frames,audio}/<stem>/`) is documented as intended behaviour in `intent.md` §Invariants — arch should weigh whether the "moving a video preserves its derivatives" property is worth the same-stem collision risk.
- **For performance reviewer:** F-sec-001 is also a performance-bounds story — `sample_count` is the dominant input on cost for `flir_hud_ocr(mode="vision")` (each frame ≈ 1.5s of ollama inference) and `detect_objects` (each frame ≈ 150-300ms YOLO). Worth checking whether the documented "5-frame default" performance bound holds when the param is uncapped at the wire boundary. Also: F-sec-005 (cache-key separator confusion) and F-sec-007 (`int(max_seconds)` truncation) cause cache misses that look like correctness bugs but read as perf regressions when the cache stops hitting.
