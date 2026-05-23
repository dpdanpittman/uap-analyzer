# Tribunal Hybrid Review — Consolidated Verdict · P-uap-v041 (verify-fix)

**Date:** 2026-05-22
**Diff:** `c267b63..7f06cb6` (uap-analyzer v0.4.1 — single fix-commit)
**Mode:** verify-the-fix against the 12 must-fix items from P-uap-v04
**Discovery:** clawpatch heuristic mapper, 6 features (unchanged from P-uap-v04 structurally — same file set)
**Stage 1 reviewers:** `tribunal-reviewer-arch`, `tribunal-reviewer-sec`, `tribunal-reviewer-perf` (lens-parallel, dispatched in two batches due to harness timing)
**Stage 2 (adversary):** `tribunal-adversary` (dispatched after trio Approve; **returned BREAKS — 3 high-confidence findings + 7 lower-severity**)

## Consolidated verdict: **BREAKS** (overridden from trio Approve by adversary stage)

The trio reached unanimous **Approve** on the verify-fix scope. The adversary then probed the **negative space** — what's outside the trio's enumerated bounds — and surfaced 3 demonstrable defects that none of the three lenses caught. Per methodology, the adversary's BREAKS verdict overrides the trio Approve. The patch should not advance to a clean re-review state without addressing the adversary's high-confidence findings (or PM-explicit residual acceptance).

## Trio synthesis (still useful as the closure ledger for the v0.4.1 scope)

| Lens         | Verdict | Items in scope | Items closed                         | New (suggestion) |
| ------------ | ------- | -------------- | ------------------------------------ | ---------------- |
| Architecture | Approve | 9              | 8 closed + 1 deferred per intent     | 3                |
| Security     | Approve | 10             | 8 closed + 2 carried over per intent | 0                |
| Performance  | Approve | 10             | 5 closed + 5 deferred per intent     | 3                |

**The trio gate opens.** Per tribunal methodology, the adversary stage runs only after a consolidated lens-trio Approve — this is the first patch from uap-analyzer to clear that gate.

## Cross-confirmed closure (the headline)

The lone Critical from P-uap-v04 — the chdir race in `detect.py:_get_model` — was confirmed closed by all three lenses independently:

- **Architecture** (`arch-F-arch-004` → closed): "no chdir, `YOLO(str(weight_path))`, lock spans entire load" — citation `detect.py:45, 82-114, 106`.
- **Security** (`sec-F-sec-003` → closed): "`os.chdir` fully removed. `YOLO(str(weight_path))` called with absolute path. The entire lookup + load + insert + LRU-evict is inside `with _MODEL_CACHE_LOCK:` — concurrent cold-starts cannot duplicate the download" — `detect.py:82-114`.
- **Performance** (`perf-F-perf-001` → closed): "`os.chdir` is absent from detect.py (grep-confirmed; only a backref comment at L103 remains). `YOLO(str(weight_path))` at `detect.py:106` with absolute path. `_MODEL_CACHE_LOCK = threading.Lock()` at L45; `with` block at L82-114 spans lookup + load + insert + eviction. Exception release via context manager."

When three independent lenses all cite the same line range and confirm closure with the same logic, that's the strongest signal Tribunal can produce that the bug is actually gone.

## Per-finding closure ledger

| Original P-uap-v04 finding                       | Severity   | Status        | Citation (file:line)                                  |
| ------------------------------------------------ | ---------- | ------------- | ----------------------------------------------------- |
| **arch-F-arch-001** compute_type in cache key    | Warning    | ✅ closed     | `audio.py:181-191`                                    |
| **arch-F-arch-002** is_image via extension       | Warning    | ✅ closed     | `detect.py:55-56, 194`                                |
| **arch-F-arch-003** width as imgsz + docstring   | Warning    | ✅ closed     | `detect.py:268, 174-177`                              |
| **arch-F-arch-004** chdir + lock (cross)         | Warning    | ✅ closed     | `detect.py:45, 82-114`                                |
| **arch-F-arch-005** hoist OllamaClient           | Suggestion | ✅ closed     | `flir.py:465-468, 502-504, 522-524`                   |
| **arch-F-arch-006** stem-only cache path         | Suggestion | ⏸️ deferred   | per intent.md (v0.4.2+)                               |
| **arch-F-arch-007** unused imports               | Suggestion | ✅ closed     | `flir.py:21, 25` now load-bearing                     |
| **arch-F-arch-008** healthz whisper fields       | Suggestion | ✅ closed     | `__main__.py:48-49`                                   |
| **arch-F-arch-009** `__version__` bump           | Suggestion | ✅ closed     | `__init__.py:3 = "0.4.1"`                             |
| **sec-F-sec-001** LAN-DOS bounds                 | Warning    | ✅ closed     | `server.py:32-39` + every tool call site              |
| **sec-F-sec-002** vision_model whitelist         | Warning    | ✅ closed     | `flir.py:35-43, 417-424`                              |
| **sec-F-sec-003** chdir worker thread (cross)    | Warning    | ✅ closed     | (same as perf-F-perf-001)                             |
| **sec-F-sec-004** stem collision                 | Warning    | ⏸️ deferred   | per intent.md (v0.4.2+)                               |
| **sec-F-sec-005** cache-key spoofing             | Warning    | ✅ closed     | `audio.py`, `detect.py`, `flir.py` cache-key paths    |
| **sec-F-sec-006** JSON RecursionError + clamp    | Suggestion | ✅ closed     | `flir.py:309-312, 338-343, 356`                       |
| **sec-F-sec-007** int truncation max_seconds     | Suggestion | ✅ closed     | `audio.py:188` (`repr()`)                             |
| **sec-F-sec-008** deploy excludes + REMOTE_DIR   | Warning    | ✅ closed     | `deploy/zaphod-deploy.sh:21-36, 57-70`                |
| **sec-F-sec-009** unauth /mcp + /healthz         | Suggestion | ⏸️ carry over | out of scope per intent.md                            |
| **sec-F-sec-010** bool isinstance guard          | Suggestion | ✅ closed     | `flir.py:290-300`                                     |
| **perf-F-perf-001** chdir race (Critical, cross) | Critical   | ✅ closed     | `detect.py:45, 82-114, 106`                           |
| **perf-F-perf-002** unbounded model cache        | Warning    | ✅ closed     | `audio.py:47-49, 112-114`; `detect.py:43-44, 110-112` |
| **perf-F-perf-003** cache not thread-safe        | Warning    | ✅ closed     | (same lock as perf-F-perf-001)                        |
| **perf-F-perf-004** per-frame OllamaClient       | Warning    | ✅ closed     | `flir.py:465-468, 502-504, 522-524`                   |
| **perf-F-perf-005** bare-int httpx timeout       | Warning    | ✅ closed     | `ollama_client.py:36-43`                              |
| **perf-F-perf-006 through -010**                 | Suggestion | ⏸️ deferred   | per intent.md (v0.4.2+)                               |

## New findings (all Suggestion-grade, non-blocking, tracked for v0.4.2+)

The trio surfaced 3 new findings during the verify-fix pass. None block this patch.

- **F-arch-101** — `_bounded()` returns a value every caller discards; reads as a clamp helper but actually only validates. Either rename to `_validate_bound()` or have callers consume the return value (e.g. for actual clamping vs. rejection).
- **F-arch-102** — `_hash_key` is duplicated across `audio.py` + `detect.py`, with the same sha256-of-joined-tuple pattern inlined a third time in `flir.py`. Three implementations of the same idiom. Extract to a shared util.
- **F-arch-103** — `_get_model` in detect.py validates against `VALID_MODELS`, then `_model_filename` does it again. Second check is dead; remove.
- **F-perf-101** — `OllamaClient` still rebuilt per-`flir_hud_ocr`-invocation; cross-invocation keepalive lost. Natural successor to arch-F-arch-005 (centralise client on `Config` for process-lifetime reuse).
- **F-perf-102** — Same `_bounded()` shape concern as arch F-arch-101 from a perf angle (calling cost of a no-op helper).
- **F-perf-103** — v2 cache-key prefix forces corpus-wide cache miss on first post-deploy session. Operator pre-warm pattern is the mitigation. Acceptable per intent.md (cache is a derivative).

## Adversary stage — BREAKS verdict

The adversary did NOT take the trio's hand-offs at face value. Instead it probed the **negative space** — what the trio _didn't_ check — and found a structural pattern: **the trio audited the patch's positive claims surface-by-surface but never probed "what's not covered?" or "what's outside the enumerated bounds?"**

### High-confidence load-bearing attacks (3)

These are demonstrable by a single REPL line or grep command. Each maps to a trio claim that turns out to be false.

#### A-001 — NaN bypasses `_bounded()` (serious)

- **Category:** `adversarial_input`
- **The trio missed:** sec claimed "every MCP tool surface that accepts the at-risk numerics calls it" and that the LAN-DOS vector was closed. Under IEEE-754, `0.0 < nan` is `False` AND `nan > cap` is `False`, so `_bounded()` returns `nan` silently. The check passes; downstream code receives `nan`. This is the strictly-worse cousin of the `bool`-is-`int` issue (F-sec-010) that sec _did_ catch in the same patch — meaning the trio fixed one Python-numeric-type quirk and missed its more dangerous sibling.
- **Severity:** serious. The patch's "LAN-DOS vector closed" claim is structurally false.
- **Defense:** add `math.isnan(value)` rejection to `_bounded()`.

#### A-002 — `analyze_pdf` and `index_corpus` are unbounded (serious, arguably critical)

- **Category:** `shared_blind_spot`
- **The trio missed:** sec's verdict said the bounded helper covers every at-risk tool. `grep -n "_bounded" src/uap_analyzer/server.py` shows it's NOT called in `analyze_pdf` (accepts `dpi`, `max_chars`) or `index_corpus`. `dpi=10000` on a multi-page PDF allocates gigapixel images per page and OOMs the container. The trio audited only the v0.4.x new tool surface; the v0.1 surface with the same shape was never inventoried.
- **Severity:** serious (LAN-DOS vector wide open in v0.1 tools).
- **Defense:** add `_bounded("dpi", dpi, 600)` + `_bounded("max_chars", max_chars, 10_000_000)` to `analyze_pdf`. Audit `index_corpus`.

#### A-003 — `describe_image` has no model whitelist (serious)

- **Category:** `shared_blind_spot`
- **The trio missed:** sec-F-sec-002 was closed for `flir.py:vision_model` via `VALID_HUD_MODELS`. The structurally identical attack surface in `src/uap_analyzer/tools/image.py:83` (`model_id = model or cfg.ollama_vision_model`, raw string straight into cache key + ollama POST) was never audited. The trio fixed the new surface and didn't probe the pre-existing one.
- **Severity:** serious. Same cache-namespace inflation + model-load attack as the one v0.4.1 explicitly claims to have fixed.
- **Defense:** apply the same `VALID_HUD_MODELS`-style whitelist (or a unified `VALID_VISION_MODELS`) to `describe_image`.

### Lower-confidence attacks (7, varying severity)

- **A-004** — `IMAGE_EXTS` omits `.tiff`/`.tif`/`.heic`/`.avif`. TIFF is canonical military FLIR distribution format.
- **A-005** — `FLIR_HUD_VISION_PROMPT` not folded into vision-mode cache key. audio + image got prompt-hashing right; flir didn't. Prompt revision invalidates cache silently.
- **A-006** — `ZAPHOD_HOST` env var is not shape-validated. A poisoned env could inject `ssh -oProxyCommand=...` for local code execution.
- **A-007** — Cache version inconsistency: flir uses `v3`, audio + detect use `v2`. The intent doc says "v1→v2" only.
- **A-008** — `_hash_key` duplicated across three files and the duplicates have **already diverged** (the v2/v3 prefix split is the proof).
- **A-009** — LRU eviction interaction with in-flight predict calls is untested. Evicting a model while it's mid-inference could corrupt state.
- **A-010** — `width=0` is accepted by `_bounded()` (the lower bound is silent — it only rejects negatives).

### The methodological lesson

The adversary's META summary: "the trio audited the patch's positive claims surface-by-surface and did not probe the negative space ('what's not covered?' and 'what's outside the enumerated bounds?')."

This is exactly what adversarial review is for. Three independent cooperative lenses can all verify "yes, the patch did X for the items it claimed" without any of them stopping to ask "but does the patch cover everything that needs X?" The adversary's mandate is precisely that question.

## What needs to happen for v0.4.2

The adversary's BREAKS verdict identifies three required fixes for a clean Approve+SURVIVES on the next pass:

1. **NaN rejection in `_bounded()`** (A-001).
2. **Audit v0.1-era tool surface for `_bounded()` coverage** — at minimum `analyze_pdf` (`dpi`, `max_chars`) and `index_corpus` (`max_chars`) (A-002).
3. **`describe_image` model whitelist** — apply the same pattern as `flir_hud_ocr` (A-003).

Plus the polish from the lower-severity finds (A-004 through A-010), tracked alongside.

Full adversary report: `.tribunal/reports/P-uap-v041/adversary-report.md`.

## Full lens reports

- Architecture: `.tribunal/reports/P-uap-v041/arch-report.md`
- Security: `.tribunal/reports/P-uap-v041/sec-report.md`
- Performance: `.tribunal/reports/P-uap-v041/perf-report.md`
- Prior verdict (P-uap-v04): `.tribunal/reports/P-uap-v04/consolidated-verdict.md`
