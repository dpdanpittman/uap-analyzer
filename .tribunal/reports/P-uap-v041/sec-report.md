# Tribunal Lens Review — Security · P-uap-v041 (Verify-the-Fix)

**Reviewer:** tribunal-reviewer-sec
**Range:** `c267b63..HEAD` (HEAD = `7f06cb6`)
**Verdict:** Approve

## Summary

v0.4.1 closes every security-lens finding from P-uap-v04 that the patch was scoped to address (F-sec-001, -002, -003, -005, -006, -007 partial, -008, -010). Two findings (F-sec-004 stem-collision, F-sec-009 unauth /mcp+/healthz) were deferred per `intent.md` and are correctly carried over.

No new security findings introduced. The `_bounded()` helper raises `ValueError` _before_ any I/O, the `VALID_HUD_MODELS` whitelist gates ollama dispatch _before_ cache touch, and the `os.chdir` race is gone (absolute weight path + `threading.Lock` covers both the cache lookup AND the YOLO load). Cache keys now sha256-hash the parameter tuple uniformly across `audio.py`, `detect.py`, and `flir.py`. The deploy script's rsync exclude set is broadened and `ZAPHOD_REMOTE_DIR` shape-validation covers absolute-path + traversal-segment checks (including the `/..//` sneaky variant).

The patch is a clean, scope-bounded fix — no scope creep, no new abstractions beyond what each finding required, and the `httpx.Timeout` split is a net improvement (5s connect = fail-fast on daemon down, generous read for legitimate long calls).

## Per-Finding Verdicts

### F-sec-001 — LAN-DOS via unbounded sample_count/width/beam_size — **closed**

- **Patch line(s):** `server.py:32-39` (helper) + `server.py:109-110, 143-144, 352-354, 404-406, 458-460` (callers).
- The new `_bounded(name, value, cap)` helper rejects `< 0` and `> cap` before any tool dispatches. Every MCP tool surface that accepts the at-risk numerics calls it:
  - `analyze_video`: count, width.
  - `extract_frame`: at_seconds, width.
  - `flir_hud_ocr`: sample_count, width, at_seconds.
  - `transcribe_audio`: beam_size, max_seconds (+ explicit length-clamp on `initial_prompt` at line 406-407).
  - `detect_objects`: sample_count, width, at_seconds (+ length-clamp on `classes` at line 461-462).
- Caps (`sample_count<=100`, `width<=4096`, `beam_size<=20`, `max_seconds<=86400`, `at_seconds<=86400`, `classes<=80`, `initial_prompt<=1024 chars`) are 1-2 orders of magnitude above realistic use — generous but bounded. **Closed.**

### F-sec-002 — vision_model whitelist — **closed**

- **Patch line(s):** `flir.py:35-43` (`VALID_HUD_MODELS` frozenset) + `flir.py:417-424` (validation before any I/O or cache).
- Whitelist is checked _before_ `cfg.resolve_corpus_path()` and _before_ the cache lookup at line 443. Default `qwen2.5vl:7b` is in the set. Mirrors the existing `VALID_MODELS` patterns in `audio.py:52-59` and `detect.py:50-53`. **Closed.**

### F-sec-003 — chdir in worker thread — **closed**

- **Patch line(s):** `detect.py:82-114`.
- `os.chdir` is fully gone from `detect.py`. `YOLO(str(weight_path))` is called with an absolute path; ultralytics handles the download to the parent dir. The entire `_get_model` body — cache lookup, model load, cache insertion, LRU eviction — runs inside `with _MODEL_CACHE_LOCK:`, so concurrent first-calls serialize cleanly. Lock release is automatic via context manager on exception. **Closed.**

### F-sec-004 — stem-only cache path collision — **carry over (deferred)**

- Per `intent.md` §Non-goals + commit body §DEFERRED: documented as intended behavior (cross-directory derivative reuse). Cache directory naming via `Path(rel_path).with_suffix("").name` is unchanged in `audio.py:197`. **Tracked for v0.4.2+.**

### F-sec-005 — cache-key `|`-separator collision spoofing — **closed**

- **Patch line(s):**
  - `audio.py:118-126` (`_hash_key` helper) + `audio.py:181-191` (usage). Inputs: `("v2", model_name, compute_type, language, beam_size, vad_filter, repr(max_seconds), prompt_hash)`. Every materially-relevant param is folded in.
  - `detect.py:117-125` (`_hash_key`) + `detect.py:199-210` (usage). Inputs: `("v2", model, t/n marker, confidence, iou, width, class_key, is_image)`.
  - `flir.py:434-441` (inline `hashlib.sha256(...)` of `("v3", mode, t/n marker, width, region_key, model_key)`).
- Raw client-controlled strings (`language`, `vision_model`, `initial_prompt`) no longer appear in the key namespace — they're either sha256-hashed (initial_prompt) or hashed-into-tuple (language, vision_model). The pipe-collision class (`language="en|b5|v1|max0|noinit"` style spoofing) is closed because the hash is over the joined string, so any change to one element changes the digest. **Closed.**

  Minor nit: in `flir.py` and elsewhere the cache_key prefix retains a few raw `|`-joined tokens (e.g. `f"v2|t{at_seconds:.2f}|{key_h}"`) — but those tokens are server-derived (mode/at_seconds/sample_count), not client-controlled-strings, so they cannot be used for spoofing. Acceptable.

### F-sec-006 — JSON-parse RecursionError + length-clamp — **closed**

- **Patch line(s):** `flir.py:309-312` (constant `_VISION_JSON_MAX_BYTES = 64 * 1024`) + `flir.py:338-343` (clamp before parse) + `flir.py:356` (catch widened to `(ValueError, TypeError, RecursionError)`).
- Vision-mode JSON is now length-clamped at 64KB before `json.loads`, and `RecursionError` is explicitly caught. **Closed.**

### F-sec-007 — int() truncation on max_seconds — **closed (via repr)**

- **Patch line(s):** `audio.py:188`. `repr(max_seconds) if max_seconds is not None else "none"` is fed into the hashed tuple. `repr(10.4)` ≠ `repr(10.9)`, so the collision is gone. **Closed.**

### F-sec-008 — zaphod-deploy.sh broader excludes + REMOTE_DIR shape — **closed**

- **Patch line(s):** `deploy/zaphod-deploy.sh:21-36` (REMOTE_DIR validation) + `deploy/zaphod-deploy.sh:57-70` (broader exclude set).
- Exclude set now covers `.env*`, `.envrc`, `secrets/`, `.secrets/`, `*.pem`, `*.key`, `*.crt`, `*.p12`, `*.pfx`, `*.gpg`, `*.asc`, `id_rsa`, `id_ed25519`.
- REMOTE_DIR validation runs _before_ the rsync invocation and uses `case` statements on:
  1. Absolute path (`/*`).
  2. No `..` traversal anywhere (`*..*`).
- Verified with manual edge-case probe (`/tmp/test_remote_dir.sh` style): `/..//etc`, `/etc/../home`, `/srv/data/..` all rejected; `/home/zaphod-beeblebox/src/uap-analyzer` accepted. The `*..*` pattern produces benign false-positives on innocuous paths like `/srv/...something` — safe-side default. Symlink-on-remote redirection is a remote-host-trust concern, not in scope for client-side validation. **Closed.**

  Note: no exclude pattern accidentally hides legitimate source files — all excludes target either secret-name patterns or build-artifact directories that the source tree doesn't carry.

### F-sec-009 — unauthenticated /mcp + /healthz — **carry over (deferred)**

- Per `intent.md` §Non-goals + commit body §DEFERRED: out of scope for v0.4.1. `server.py:54-56` still disables DNS-rebinding protection, `__main__.py:38-52` still serves `/healthz` unauthenticated. LAN-trust posture unchanged. **Tracked for v0.4.2+.**

  Cross-reviewer note for adversary stage: this is the only finding that, if exploited, would make the rest of the v0.4.0 inference surface (CPU/disk consumption) reachable from off-LAN. Worth flagging as the priority-1 item for v0.4.2.

### F-sec-010 — isinstance bool guard in \_normalize_vision_fields — **closed**

- **Patch line(s):** `flir.py:290-300`. Each of the three numeric fields (`range_nm`, `bearing_deg`, `elevation_deg`) now has an explicit `and not isinstance(rng, bool)` guard before the `(int, float)` check. Comment at line 287-289 documents the why. **Closed.**

## New Security Posture Audit

### Does `_bounded()` ValueError propagate cleanly through the MCP layer?

- Yes. `_bounded()` raises plain `ValueError` with factual messages (`"sample_count must be <= 100; got 99999"`). FastMCP's `@mcp.tool()` decorator converts handler exceptions into tool-error responses containing the exception message — no Python traceback is exposed to the MCP client by default. Messages are factual and contain no sensitive paths or internal state. **No stack-trace leak.**

### Does the 5s `connect` timeout open new races (ollama warming up, model load time)?

- No. `connect=5.0` is the **TCP connect** phase only — ollama's HTTP listener accepts the socket within milliseconds once the daemon is up, regardless of model-load latency. Model-load and inference time land in the `read` phase, which is governed by `cfg.ollama_timeout` (300s default). If ollama is in cold-startup (process started, listener not yet bound), 5s is the failure case and the right one — a fail-fast signal rather than a 25-min vision-mode FLIR hang. **No new race.**

### Does ZAPHOD_REMOTE_DIR validation cover sneaky variants?

- Tested manually:
  - `/..//etc` → rejected (`*..*` matches).
  - `/etc/../home` → rejected.
  - `/srv/data/..` → rejected.
  - `../etc` → rejected (not absolute).
  - `` (empty) → rejected (not absolute).
  - `/home/zaphod-beeblebox/src/uap-analyzer` → accepted.
- The `*..*` glob is intentionally broad and will reject benign paths like `/srv/...legit/...` — that's an acceptable safe-side default. **Symlinks on the remote** are outside the script's authority (it's an rsync target, not a chroot enforcer); that's a remote-host-trust concern and not in scope. **Validation is sound.**

### Do any new rsync exclude patterns accidentally hide legitimate source?

- Scanned the patterns: all secret-name patterns (`*.pem`, `id_rsa`, etc.) are not present in the source tree. Directory patterns (`secrets/`, `.secrets/`) target deploy-side, not source-side. Build-artifact patterns (`dist`, `build`, etc.) target generated content. **No false negatives on legitimate source files.**

## Cross-Reviewer Ready Notes

- **For architecture reviewer:** the `_bounded` helper duplicates a small validation contract across every numeric tool argument; this is fine for v0.4.1 (matches the patch's narrow scope) but if v0.4.2 adds another numeric arg the call-site repetition will start to read as boilerplate. Worth a future decorator-based approach.
- **For performance reviewer:** the new `threading.Lock` in `detect.py:_get_model` is held across the YOLO download/load on cold start (potentially tens of seconds for the first call). A second concurrent caller will block on it. This is _correct_ (it's how the perf-F-perf-001 race was meant to be closed) and the cap is one-time, but worth noting that two-cold-start scenarios serialize. Same shape in `audio.py:_get_model`.
- **For adversary stage:** F-sec-009 (unauth /mcp) remains the single largest leverage point. If an adversary can route into the LAN, the v0.4.0 inference surface is fully reachable. The `_bounded()` caps limit per-call cost but not request rate — there's no per-tool rate limit. Worth probing in the adversary stage as a CPU-exhaustion / disk-fill scenario even with the new caps in place (e.g. 100 sample_count × N parallel calls × M times).

## CI / Test Status

- Patch ships 6 new unit tests covering the v0.4.1 surfaces (`test_detect_is_image_classifies_by_extension`, `test_detect_model_cache_lru_eviction`, `test_detect_hash_key_collision_resistant`, `test_flir_vision_model_whitelist_enforced`, `test_flir_normalize_rejects_bool_for_numeric_fields`, `test_server_bounded_helper_rejects_overflow`). Commit body says 28/29 pass locally (the 1 failure is the pre-existing `test_imports` mcp+httpx stack issue, container-only). No related CI is red.

## Verdict

**Approve.** All in-scope security-lens findings are closed with file:line evidence. Deferred items (F-sec-004, F-sec-009) match the intent doc. No new attack surface introduced. The trio should approve and the adversary stage gets the gate.
