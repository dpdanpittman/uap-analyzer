# Plan — uap-analyzer v0.4.1 patch

## Plan registry

- **ID:** P-uap-v041
- **State:** InReview
- **Owner:** dpdanpittman
- **Working branch:** main
- **Review cwd:** ~/src/uap-analyzer
- **Review range / Diff basis:** `c267b63..HEAD` (HEAD = `7f06cb6`)
- **Acceptance criteria:** all 12 must-fix items from P-uap-v04 verifiably closed by the patch + no regression on the v0.4.0 surface. Trio Approve gate opens the adversary stage.

## What this patch ships

The patch is in a single commit (`7f06cb6`) and addresses the consolidated verdict at `.tribunal/reports/P-uap-v04/consolidated-verdict.md`:

- **Critical (1, cross-confirmed by all three lenses):** chdir race in `detect.py:_get_model` — fixed via absolute weight path + `threading.Lock`.
- **Warnings (11 of 14 closed in this pass):**
  1. compute_type added to whisper cache key
  2. is_image via extension classification (`IMAGE_EXTS` frozenset)
  3. width passed as `imgsz=` to YOLO + docstring corrected
  4. LRU-bounded `_MODEL_CACHE` (audio + detect) at 3 entries
  5. `_MODEL_CACHE` thread-safe (same lock as the chdir fix)
  6. Split `httpx.Timeout(connect=5, read=cfg.ollama_timeout, write=10, pool=10)`
  7. MCP-boundary numeric clamps via `_bounded()` helper
  8. `vision_model` whitelist via `VALID_HUD_MODELS`
  9. Cache-key sha256 hashing across all three new tools
  10. Deploy script: broader secret excludes + `ZAPHOD_REMOTE_DIR` shape validation
  11. Hygiene: version bump, healthz whisper fields, bool guard, RecursionError catch

## What this patch deliberately does NOT address

Deferred to v0.4.2+ (recorded in v0.4.1 commit message):

- perf-F-perf-006: vision-mode sequential loop (asyncio.gather option)
- perf-F-perf-007: cache-row bloat for long transcripts
- perf-F-perf-008: redundant per-region preprocess in flir.py
- perf-F-perf-009: per-frame ffmpeg subprocess in extract_frame
- perf-F-perf-010: observability gap during model downloads
- sec-F-sec-004: stem-only cache path collision (documented as intended)
- sec-F-sec-007: int() truncation on max_seconds (partially addressed: repr() now used)
- sec-F-sec-009: unauthenticated /mcp + /healthz (out of scope per intent)

## Review dispatch

Lens stage routed through agentic Task dispatch (no Anthropic API key in this session). Three reviewers in parallel: arch, sec, perf. If trio approves → adversary stage. Findings → `.tribunal/reports/P-uap-v041/`.

## Out of scope

- The v0.1 base tools (untouched by the patch).
- The corpus directory state (the Release_1 reorg was data, not code).
- The mabus.ai / mabus-os / session-loam / tribunal work in the same session.
