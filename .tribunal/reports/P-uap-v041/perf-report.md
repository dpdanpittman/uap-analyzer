# Tribunal Lens Review — Performance · P-uap-v041 (verify-the-fix)

**Reviewer:** tribunal-reviewer-perf
**Range:** `c267b63..HEAD` (HEAD = `7f06cb6`)
**Verdict:** Approve

## Summary

All five performance-lens findings the v0.4.1 patch was scoped to address close cleanly with surgical, traceable hunks. The cross-confirmed Critical (chdir race in `detect.py:_get_model` — perf-F-perf-001) is resolved by passing an absolute weight path to `YOLO()` and serialising the entire get-or-load body under `threading.Lock`. The unbounded `_MODEL_CACHE` (perf-F-perf-002) is now an `OrderedDict` capped at 3 with LRU eviction inside the lock; the concurrent first-call duplicate-load race (perf-F-perf-003) is closed by the same lock. The per-frame `OllamaClient` build (perf-F-perf-004) is hoisted to one client per `flir_hud_ocr` invocation with lifecycle owned by the caller, closed in `finally`. The bare-int httpx timeout (perf-F-perf-005) is now `httpx.Timeout(connect=5, read=cfg.ollama_timeout, write=10, pool=10)` — fail-fast on a downed daemon, generous read for legitimate long inferences.

The five Suggestion-grade items (perf-F-perf-006 through perf-F-perf-010) are explicitly deferred per `intent.md:82-84` and the v0.4.1 commit body §DEFERRED. The deferral is appropriate — none gates the trio-approve gate, and the cross-reviewer hand-offs raised by arch + sec are addressed below.

No new performance regressions introduced. The arch lens already approved; the sec lens already approved; with this perf approval the trio gate opens for the first time on uap-analyzer.

## Closure ledger

| Original finding | Severity   | Claim                                                                        | Status       | Citation                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------- | ---------- | ---------------------------------------------------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| perf-F-perf-001  | Critical   | chdir gone, absolute path passed to YOLO, lock covers full load              | **closed**   | `os.chdir` absent from `src/uap_analyzer/tools/detect.py` (only a backref comment at L103 survives). `detect.py:106` is `model = YOLO(str(weight_path))` with an absolute path. `detect.py:45` declares `_MODEL_CACHE_LOCK = threading.Lock()`. `detect.py:82-114` holds the lock across lookup + `YOLO()` load + LRU eviction. Exception path: `with` block releases on raise.                                          |
| perf-F-perf-002  | Warning    | LRU-bounded `_MODEL_CACHE` at 3 entries in both audio + detect               | **closed**   | `detect.py:43-44` declares `_MODEL_CACHE_MAX = 3` and `_MODEL_CACHE: OrderedDict[str, Any] = OrderedDict()`. Eviction loop at `detect.py:110-112` (`while len > MAX: popitem(last=False)`). Mirrored in `audio.py:47-49` and `audio.py:112-114`. Cap enforcement is **inside** the lock so eviction races can't leak.                                                                                                    |
| perf-F-perf-003  | Warning    | `_MODEL_CACHE` thread-safe via same lock as -001                             | **closed**   | `detect.py:82` `with _MODEL_CACHE_LOCK:` wraps the recheck (L83-85), the load (L106), the insert (L108), and the eviction (L110-112). `audio.py:101-115` mirrors the pattern. Concurrent first-callers serialize — the second caller hits a warm cache entry after the first's load completes.                                                                                                                           |
| perf-F-perf-004  | Warning    | Per-frame `OllamaClient` hoisted to one-per-invocation                       | **closed**   | `flir.py:465-468` builds the client once per `flir_hud_ocr` invocation (vision mode only). `flir.py:502-504` passes it to `_vision_extract_frame` instead of building inside. `flir.py:522-524` closes once in `finally`. `_vision_extract_frame` signature changed from `(cfg, frame_path, *, model=None)` to `(client, frame_path, *, model)` — caller-owns-lifecycle. TCP keepalive preserved across the frame sweep. |
| perf-F-perf-005  | Warning    | Split `httpx.Timeout(connect=5, read=cfg.ollama_timeout, write=10, pool=10)` | **closed**   | `tools/ollama_client.py:36-43`. Bare `httpx.AsyncClient(timeout=cfg.ollama_timeout)` is gone. A downed ollama daemon now fails fast on connect (5s) instead of waiting the full 300s envelope; 5-frame vision sweep against a down daemon: 25s total instead of the previous 25-minute hang. Comment at L29-35 cites the finding.                                                                                        |
| perf-F-perf-006  | Suggestion | Vision-mode sequential loop → `asyncio.gather`                               | **deferred** | Per `intent.md:82` and commit body §DEFERRED. No code change in this patch; `flir.py:472-521` retains the sequential loop. Acceptable: ollama's default `OLLAMA_NUM_PARALLEL=1` means gathering wouldn't help on the current single-stream config. Tracked for v0.4.2+. No regression.                                                                                                                                   |
| perf-F-perf-007  | Suggestion | Cache-row bloat for long transcripts                                         | **deferred** | Per `intent.md:82`. `audio.py:236-247` still stores full transcript JSON. Tracked. No regression.                                                                                                                                                                                                                                                                                                                        |
| perf-F-perf-008  | Suggestion | Redundant per-region preprocess                                              | **deferred** | Per `intent.md:82`. `flir.py:128-152` unchanged. Tracked. No regression.                                                                                                                                                                                                                                                                                                                                                 |
| perf-F-perf-009  | Suggestion | Per-frame ffmpeg subprocess                                                  | **deferred** | Per `intent.md:82`. `video.py` unchanged in this patch. Tracked. No regression.                                                                                                                                                                                                                                                                                                                                          |
| perf-F-perf-010  | Suggestion | Observability gap during model downloads                                     | **deferred** | Per `intent.md:82`. `audio.py:106-109` and `detect.py:105` still single-line at start of load. Tracked. No regression.                                                                                                                                                                                                                                                                                                   |

All five in-scope perf findings: **closed**. Five Suggestion-grade items: **deferred-by-design**.

## Cross-reviewer hand-offs received

### Arch handed: `_MODEL_CACHE_LOCK` held across cold-start download serialises different-model loads

- **Source:** `arch-report.md` "Cross-Reviewer Ready Notes" §For performance reviewer (bullet 1).
- **Concern:** the lock covers the YOLO download path (~30-90s cold start). Two concurrent callers for **different** models serialize, not parallelise.
- **Verdict:** **non-blocking, intentional.** This is the shape the perf-F-perf-001 fix explicitly recommended ("wrap the `_MODEL_CACHE` check+insert in a `threading.Lock` guarded singleton-load"). The reason the lock has to span the load:
  - Without it, two threads both miss the cache, both `YOLO(weight_path)` against the same target file, both trigger ultralytics' download-to-`<name>.pt.tmp`-then-rename. The torn-rename window is the exact corruption case perf-F-perf-001 cited.
  - A per-model-name lock (a `dict[str, threading.Lock]`) would let different-model cold-starts parallelise, but adds its own meta-lock for `LOCKS.setdefault(name, Lock())`. For a single-operator workflow (intent.md §"single-caller, container-long-lived assumption") the contention scenario is theoretical — Dan is one operator hitting one tool at a time. The lock acquisition itself is uncontended steady-state; the contention only matters during the one-time cold-start window.
  - The deferred sequential-vision-loop (perf-F-perf-006) already acknowledges ollama's single-stream serialization as the dominant bottleneck for vision-mode workflows. Cold-start serialization is dominated by the same single-stream constraint.
- **Action:** documented in this report; no code change recommended for v0.4.1. **Flagged for v0.4.2+ as a "if multi-operator usage ever materialises" deferred item**, alongside the existing perf-006 deferral.

### Sec handed: same `connect=5s` doesn't open a new race against an ollama-cold-startup window

- **Source:** `sec-report.md` "New Security Posture Audit" §Does the 5s connect timeout open new races.
- **Concern:** if ollama is in process-startup (binary launched, listener not yet bound), a 5s connect could fail spuriously.
- **Verdict from sec:** "No. `connect=5.0` is the TCP connect phase only — ollama's HTTP listener accepts the socket within milliseconds once the daemon is up, regardless of model-load latency."
- **Perf concurrence:** confirmed. Ollama's listener is up before it does any model load (model loads happen lazily on first `/api/chat` POST, during the `read` phase governed by `cfg.ollama_timeout`). A 5s connect timeout is correctly the fail-fast signal for "daemon not running" rather than "daemon warming up." No new race introduced.

### Arch handed: `_hash_key` sha256 cost is microseconds; cache-miss window post-deploy is 100%

- **Source:** `arch-report.md` "Cross-Reviewer Ready Notes" §For performance reviewer (bullet 3).
- **Concern:** v2 cache-key prefix means all v1 entries are orphaned. First session post-deploy has 100% cache miss until repopulation.
- **Verdict:** **acceptable.** Cache is a derivative — cache misses re-populate on first call, and the v1 → v2 prefix bump is the right shape for the correctness fix (sec-F-sec-005 closure). The repopulation cost is bounded by the corpus size × the new-tool surface (3 tools × 220 items × per-call cost, but only for items the operator actually re-runs). `intent.md:75-76` explicitly acknowledges and accepts this: "older cache entries become orphaned. Was that acceptable? (Yes — the cache is a derivative.)"
- **Action:** no code change; flagged here for operator awareness — a `corpus.purge_orphaned_cache()` housekeeping tool would be a perf-suggestion for v0.4.2+ if the SQLite cache row count starts mattering.

## New performance findings introduced by v0.4.1

### F-perf-101 — `OllamaClient` is still rebuilt per-`flir_hud_ocr`-invocation (cross-invocation churn)

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/tools/flir.py:465-468`
- **What:** v0.4.1 correctly hoists the client out of the per-frame loop so TCP keepalive is preserved across frames within one `flir_hud_ocr` call. But each invocation still constructs a fresh client (`OllamaClient(cfg)` builds a new `httpx.AsyncClient` with a fresh connection pool). For a workflow that calls `flir_hud_ocr` repeatedly across the corpus (`for video in corpus: flir_hud_ocr(video, mode='vision')`), the keepalive is dropped between invocations.
- **What blows up:** Nothing critical — on the localhost-to-host-network path (host ollama at 192.168.6.56:11434, container on host-net), TCP handshake is microseconds. On a remote-ollama deployment, the per-invocation handshake is one extra round-trip per video. The shape is consistent with `describe_image`/`analyze_pdf` which also build per-call clients, so this isn't a regression — just a not-yet-optimised pattern.
- **Suggested defense:** Build a single `OllamaClient` on the `Config` long-lived object (lifecycle-bound to `build_server`), and pass it to tool handlers. This is the `cfg`-scoped client cache arch already flagged as a future refactor. Defer to v0.4.2+ if/when the cross-invocation pattern becomes the dominant workflow.

### F-perf-102 — `_bounded()` doesn't actually clamp (matches arch F-arch-101 from a perf angle)

- **Severity:** Suggestion
- **Location:** `src/uap_analyzer/server.py:32-39` and every call site (`server.py:109-110, 143-144, 352-354, 404-405, 458-460`).
- **What (perf angle):** arch already filed this as F-arch-101 from a contract-shape angle. The perf concern is adjacent: every MCP tool call now pays 1-3 `_bounded()` invocations on the request path. Each call is a comparison + raise-or-fallthrough — sub-microsecond cost, but the shape (function call + name-string allocation for the error message) is heavier than a single bare `if width > _MAX_WIDTH: raise`. On a hot tool surface this matters approximately zero; on a cold one, even less. Filing as a Suggestion so a future maintainer who consolidates the bound checks (per arch's recommendation to use a decorator) can do it with both lenses' framing.
- **Suggested defense:** Defer until the call-site repetition starts to read as boilerplate (sec's hand-off also flagged this). The current shape is fine for v0.4.1's scope.

### F-perf-103 — v2 cache prefix forces a corpus-wide cache miss on first deploy

- **Severity:** Suggestion
- **Location:** All cache-key generation sites: `audio.py:191`, `detect.py:204, 210`, `flir.py:437, 442`.
- **What:** Already covered in the cross-reviewer hand-off above; filing here as a finding so it's tracked. The v1 → v2 (and v2 → v3 in flir) prefix bump correctly invalidates the old (collision-spoofable) cache namespace, but the first post-deploy invocation of each of the three new tools (`transcribe_audio`, `detect_objects`, `flir_hud_ocr`) will miss the cache and pay the inference cost again. For the 220-corpus envelope this is a one-time bounded cost — operator pre-warming via a one-shot loop after deploy is the natural mitigation.
- **What blows up:** First-session-after-deploy latency. Bounded by `(corpus_items × tools_called × per-call inference cost)`. With the YOLO/whisper LRU at 3 entries, the model-load cost is amortised within the session.
- **Suggested defense:** Document the post-deploy warm-up pattern in README (`for vid in corpus: detect_objects(vid); flir_hud_ocr(vid); transcribe_audio(vid)` once after a v0.4.x upgrade). Optional: add a `corpus.warm_cache(tools=[...])` housekeeping tool in v0.4.2+. No blocking concern.

## Concrete-scenario walkthroughs

These are the perf-lens versions of intent.md §"Concrete scenarios":

1. **Concurrent first-call to detect_objects with two different models** (intent §1).
   - Path: caller A enters `_run_detect` → `_get_model(cfg, "yolov8n")` acquires `_MODEL_CACHE_LOCK`. Caller B enters concurrently and blocks at `with _MODEL_CACHE_LOCK:`.
   - A passes `YOLO(str(weight_path_n))` (absolute path), download lands at `weight_dir/yolov8n.pt`, model registered in cache, A releases lock. B acquires, sees `yolov8s not in cache`, calls `YOLO(str(weight_path_s))`, download lands at `weight_dir/yolov8s.pt`. No cwd mutation, no `.tmp` rename race, both succeed.
   - **Confirmed.** Cold-start is serialised by design; the alternative (parallel cold-starts) is the race perf-F-perf-001 closed.

2. **Vision-mode FLIR sweep with ollama daemon down** (corollary to perf-F-perf-005 closure).
   - Path: `flir_hud_ocr(mode="vision", sample_count=5)`. Pre-patch: 5 × per-frame `OllamaClient(cfg)` with bare-int `timeout=300`. Each frame waits the full 300s connect phase before raising `OllamaError`. 25-min hang.
   - Post-patch: one `OllamaClient(cfg)` built once. First `client.describe_image()` → httpx tries connect, fails in 5s, raises `OllamaError`. Subsequent frames hit the same client; httpx pool retries connect each frame, 5s × 5 = 25s total. Lock the math: 25s is the new failure envelope vs 25min. **Confirmed.**

3. **Operator toggles `WHISPER_COMPUTE_TYPE=int8 → float16` between deploys** (intent §2).
   - Path: cache key now includes `cfg.whisper_compute_type` (`audio.py:184`). Old `int8` cache row keyed by hashed tuple including `"int8"`; new request keyed by hashed tuple including `"float16"`. Distinct hash. `corpus.get_cached(...)` returns None. Fresh inference, fresh row.
   - Module-level `_MODEL_CACHE` keyed by `(model_name, compute_type)` — both compute_type variants can coexist in cache (up to LRU cap of 3), so re-toggling is cheap.
   - **Confirmed.**

4. **Operator A/B-sweeps yolov8n → yolov8s → yolov8m → yolov8l → yolov8x** (perf-F-perf-002 corollary).
   - Path: each call `_get_model(cfg, name)` enters the lock. First 3 fill the LRU. The 4th (yolov8l) triggers eviction of yolov8n (least-recently-used by insertion order; no `move_to_end` happened for it because it was never re-accessed). 5th (yolov8x) evicts yolov8s.
   - Total resident: max 3 × largest variant ≈ 3 × 136MB = ~400MB worst case (yolov8m + yolov8l + yolov8x). Pre-patch: all 5 resident ≈ 300MB + torch buffers, never freed.
   - **Confirmed.**

## Cross-Reviewer Ready Notes

- **For architecture reviewer:**
  - Confirmed arch's F-arch-101/-102/-103 are all Suggestion-grade non-blockers. The `_bounded()` return-value shape (F-arch-101) is the same concern I filed as F-perf-102 — different angle, same fix recommendation.
  - The `_hash_key` duplication (F-arch-102) is a refactor opportunity, not a perf concern. Consolidating it has microsecond-scale perf impact; the consolidation case is purely architectural.
  - The deferred F-perf-101 (cross-invocation `OllamaClient` churn) is the natural successor to arch-F-arch-005. When arch revisits "centralize `OllamaClient` on `Config`," the perf wins are bounded by the cross-invocation handshake cost on the current host-network topology.

- **For security reviewer:**
  - Confirmed sec's verdict on the 5s connect timeout — no new race introduced. ollama's listener-up-before-model-load behavior is the reason this works.
  - The 25-min → 25s vision-mode hang reduction (perf-F-perf-005 closure) is also a DOS-mitigation: a LAN client can no longer pin server resources for 25min/call by routing requests to a half-up ollama. Cross-validates sec's F-sec-001 closure.
  - The deferred F-perf-006 (asyncio.gather) is worth a sec-lens glance when it lands — gathering N vision calls against ollama with `OLLAMA_NUM_PARALLEL>1` would change the connection-pool exhaustion math.

- **For adversary stage:** the new caps (`_bounded()`) limit per-call cost but not request rate. Adversary stage should probe `100 sample_count × N parallel calls × M repetitions` to confirm the perf envelope holds under burst load. The `_MODEL_CACHE_LOCK` serialization means concurrent first-callers queue rather than parallelise — adversary stage should confirm that's the expected shape (it is, per intent.md §"single-caller assumption") and that queue depth doesn't trigger unbounded waiter accumulation. The httpx `pool=10` timeout means waiters > 10s for a free pool slot get rejected, which is the right shape for adversary-load handling.

## CI / Test Status

- Patch ships `test_detect_model_cache_lru_eviction` at `tests/test_smoke.py:444-464` covering the perf-F-perf-002 LRU eviction shape. The test sneaks fake entries past `_get_model` to validate the eviction path without paying the YOLO load cost — appropriate test surface (the perf concern is the cache shape, not the model behavior).
- `test_detect_hash_key_collision_resistant` at `test_smoke.py:467-483` covers the sec-F-sec-005 cache-key hashing, which the perf lens also relies on (orphaned-v1-cache → 100% miss on first post-deploy session, per F-perf-103).
- No dedicated unit test for the `_MODEL_CACHE_LOCK` serialization shape (would require a threaded test harness) — acceptable for v0.4.1, the lock semantics are stdlib-trusted and the code path is small.
- No dedicated unit test for the `httpx.Timeout` split — would require mocking `httpx.AsyncClient` and asserting the timeout shape. Acceptable for v0.4.1; the change is a one-line shape swap and the failure mode is observable in production logs as a fast `OllamaError` instead of a 5-min stall.

## Verdict rationale

All five in-scope performance findings from P-uap-v04 close with file:line evidence:

- perf-F-perf-001 (Critical, cross-confirmed): chdir gone, `YOLO(str(weight_path))`, lock spans the full load. **Closed.**
- perf-F-perf-002 (Warning): LRU at 3 entries with eviction inside the lock. **Closed.**
- perf-F-perf-003 (Warning): same lock as -001, covers concurrent first-call to the same key. **Closed.**
- perf-F-perf-004 (Warning): client hoisted, lifecycle owned by `flir_hud_ocr`, `finally`-closed. **Closed.**
- perf-F-perf-005 (Warning): `httpx.Timeout` split, connect=5s, read=cfg.ollama_timeout. **Closed.**

Five Suggestion-grade items (perf-006 through perf-010) are deferred-by-design per `intent.md`. Three new Suggestion-grade items (F-perf-101 cross-invocation client churn, F-perf-102 `_bounded` shape, F-perf-103 v2-prefix cache-miss window) are filed but non-blocking — they're polish, post-deploy operator-facing notes, and a future refactor opportunity, respectively. None reaches Warning grade.

Cross-reviewer hand-offs from arch + sec are addressed in the per-handoff section: the lock-held-across-cold-start serialization is the intended shape of the perf-F-perf-001 fix (alternative parallel-load designs reintroduce the download race); the 5s connect timeout is correctly fail-fast and doesn't open a new race against ollama-cold-startup.

The arch lens approved. The sec lens approved. With this perf approval, the trio Approve gate opens for the first time on uap-analyzer.

**Approve.**
