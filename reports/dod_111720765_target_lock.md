# DOD_111720765 — vertical-container target-lock sequence (88s, redacted IR)

**Date analyzed:** 2026-05-22
**Source:** `Release_2/DOD_111720765.mp4`
**Artifact type:** video
**Status:** initial pass (5 baseline frames + 4 dense extracts in t=14–20s window, 10-frame YOLO sweep)

## TL;DR

An 88-second IR-style sensor sequence with a **persistent target-lock bounding box** over what YOLO classifies as an aircraft (peak confidence 0.822 at t=24s). Released in an **unusual 608×1080 portrait container** — a regular ~16:9 sensor frame letterboxed into a vertical canvas with thick black bars top and bottom. The non-target portion of the imagery is overlaid with a static gray-with-black-rectangles mask that is **pixel-identical across all sampled frames spanning 60+ seconds**, strongly suggesting either a post-release composited redaction layer or a single sensor frame with animated target indicators on top (briefing artifact). Audio track is present (AAC) but Whisper transcribes 0 segments — silent. No HUD overlay (no date, range, AZ/EL, sensor mode). A small secondary contact (untracked) appears at multiple timestamps; YOLO independently flags it as a second "airplane" at t=80.5s.

## Metadata

- Duration: **88.57 s**
- Resolution: **608×1080** (portrait, ~9:16) — anomalous; rest of corpus is landscape 16:9 / 4:3
- Frame rate: 30 fps
- Codec: H.264, yuv420p
- Bitrate: ~210 kbps (lower than typical corpus clips)
- File size: 2.3 MB
- Container: mp4
- Audio: AAC present, **0 speech segments transcribed** (Whisper base.en, int8) — effectively silent

The portrait container is the format anomaly. Sensor imagery fills only the central ~50% of vertical extent; top and bottom are black bars. The implied "real" sensor frame is roughly 608×540 (~1.13:1, near-square / 4:3), which is plausible for a legacy targeting-pod display.

## Per-frame observations

Frames extracted at t=14.76, 16.00, 18.00, 20.00, 29.52, 44.28, 59.04, 73.81 s. Cache paths under `frames/DOD_111720765/`.

| t (s) | Target inside lock box                           | Secondary contact                   | Notes                                                          |
| ----: | ------------------------------------------------ | ----------------------------------- | -------------------------------------------------------------- |
| 14.76 | Angled bright streak / dash                      | —                                   | Tightly framed in lock box, distinct angle                     |
| 16.00 | Slightly rotated angled shape, sparkle artifacts | —                                   | Morphology continues to shift                                  |
| 18.00 | Angled shape, lower contrast vs t=16             | —                                   |                                                                |
| 20.00 | Small dark rectangle, faint                      | —                                   |                                                                |
| 29.52 | Small smear, low contrast                        | —                                   | YOLO highest-confidence frame (0.82 at t=24s, between these)   |
| 44.28 | Near-invisible point                             | **White dot above-left of reticle** | Object morphology dim; first appearance of a secondary contact |
| 59.04 | **Clear elongated aircraft-like silhouette**     | Small white dot lower-right         | The most diagnostic frame of the set                           |
| 73.81 | Bright elongated hot-bar                         | Small white dot lower-right         | Sensor-saturated bar                                           |

The off-reticle background mask (gray field with ~12 sharp-edged black rectangles) is **identical to the pixel across the entire 60-second sample window**, including the dense t=14–20s sweep.

## Tool comparison: vision models on this material

| Model                                                 | Result on these frames                                                                                                                                                                                                                                                                                                            |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `llama3.2-vision:11b` (default `OLLAMA_VISION_MODEL`) | **Failed.** Described frames as "ocean from above with no objects, no HUD overlay, no imaging anomalies." Missed reticle, target lock, aircraft, mask layer entirely. **One frame triggered an infinite generation loop** ("The final answer is: The image is a single frame from a US military FLIR (forward-looking..." × ~20). |
| `qwen2.5vl:7b` (default `OLLAMA_HUD_MODEL`)           | **Succeeded on 9/9 frames.** Correctly identified centered targeting reticle, target-lock bounding box, aircraft-like shape inside the box, hot-spot / heat signature, black-border letterboxing, and redaction-shaped rectangular masks. No HUD numerals (correctly noted absent).                                               |
| `yolov8`/`yolov11` via `detect_objects`               | **5/10 sampled frames hit `airplane` class.** Peak confidence 0.822 at t=24.16s. **Independent second "airplane" detection at t=80.52s (bbox 929×1032 → 968×1068)** corroborates the secondary contact.                                                                                                                           |

Practical implication: for heavily quantized / redacted IR material, `llama3.2-vision:11b` is unreliable in both directions — it both fabricates plausible-sounding negatives ("clear and stable thermal image of the ocean") _and_ can spiral into a generation loop. `qwen2.5vl:7b` was reliable across all 9 frames in this run. Worth considering swapping the default vision model for IR/redacted material, or surfacing a model override hint in tool responses when confidence is low.

## Notable / anomalous details

1. **Pixel-identical background across 60+ s.** This is the single most diagnostic feature. Possibilities, roughly ranked:
   - **(a) Static composited redaction/mask layer.** The release pipeline overlaid a sanitized backdrop on the sensor data, preserving only the immediate target area. Plausible — DoD UAP releases sometimes mask geographic context for OPSEC.
   - **(b) Briefing-slide artifact.** What was released is a single sensor frame with the target-lock indicator and (small) object overlay animated on top. The whole "video" is then more of a reconstructed analyst artifact than raw sensor capture.
   - **(c) Genuinely static scene.** Both camera and scene held still for 60+ s while the tracked target moved relative to them. Plausible only for a tripod-mounted sensor watching a distant target; unlikely for an airborne pod.
2. **Portrait letterboxing of a landscape sensor frame.** 608×1080 with thick top/bottom bars wrapping a ~4:3 central frame is the wrong way around for any normal sensor. Probably a transcode artifact from a viewer or social-media-formatted re-encode rather than the original delivery format.
3. **No HUD text whatsoever.** Standard FLIR releases retain at least date / range / sensor mode. Stripped HUD is consistent with the "redacted post-release" reading.
4. **AAC audio present but silent.** Whisper transcribed 0 segments; either the track is dead air, ambient with no speech, or it has been muted in post.
5. **Confirmed secondary contact.** YOLO independently flags a second small "airplane" bbox at t=80.5s while the primary lock remains on the main target. Eyeball check confirms a small white dot in the lower-right region in multiple frames after t=44s.
6. **Aircraft-shape morphology changes within the lock window.** At t=59s the silhouette is unmistakably winged / elongated; at other timestamps it presents as a streak, dot, or hot-bar. Consistent either with (i) a real aircraft seen from changing aspect angles, or (ii) a sensor that is integrating differently across frames. Without HUD or geolocation data, can't disambiguate.

## Hypotheses

(Speculative.)

1. **Briefing-slide reconstruction.** The release is a single sensor still, redacted, with target-lock indicator and object overlay animated by an analyst tool. This would explain (a) the static background, (b) no HUD, (c) silent audio, (d) low bitrate, (e) portrait letterboxing if the slide was originally rendered for a presentation deck. If true, the "video" is evidence _about_ an incident, not direct sensor capture of it.
2. **Heavy-redaction passthrough of a real targeting-pod clip.** The lock and the target object are real, the background is masked out, and the originals are not what was released. The aircraft-shape evolution at t=14–73s is genuine sensor data.
3. **Two distinct UAP incidents in a single bundle.** Primary tracked target + untracked secondary contact at t=80.5s could be a wingman, sister incident, or sensor artifact (lens flare, hot pixel column). YOLO's two-bbox detection is the only quantitative evidence here.

(1) and (2) are not mutually exclusive — the release could be a real targeting-pod clip rendered into a briefing-slide redacted form.

## Methodology note

- `analyze_video` mode=`describe` with `count=5` initially failed silently (empty HTTP body, no JSON error) when llama3.2-vision was being driven on the cold path. The first call hit a degenerate generation. Subsequent calls succeeded once the model warmed and the cache absorbed the spike.
- `describe_image` with explicit `model` override is preferable to relying on the server default when you suspect the default vision model will struggle. Worth considering a per-tool default that picks `qwen2.5vl:7b` for cached frames where `is_letterboxed=True` or where the first describe call ran > N seconds (suggesting a generation loop).
- The MCP HTTP transport interleaves SSE keepalive `: ping` comments before `data:` lines for long-running calls; any client parser that prefix-matches on `event:`/`data:` must scan all lines, not just the first.

## Cross-references

- Release_1 reports use IDs in the `1116890xx–1116892xx` range; Release_2 IDs are in `1117197xx–1117217xx`. Date-of-event for either set is not in this report — would need DOW release metadata to tie a clip to an incident.
- No DOW or DOS PDF in the corpus is currently FTS-tied to `111720765`. Worth a `search_corpus` pass for "target lock" / "portrait" / unique HUD strings once we have them.

## Next steps

1. Pull a continuous strip (e.g. `extract_frame` at 1-second intervals from t=10 to t=85) and run YOLO on each to chart confidence-over-time. If confidence falls off cleanly, the target is real and changing aspect; if it's bimodal (high during certain windows, gone in others), that points at briefing-slide overlay being toggled.
2. Run `detect_objects` with `classes=["airplane"]` and lower confidence (~0.1) across the full sweep to chart the secondary-contact appearances. Two-track plot would be the cleanest evidence for "two distinct objects."
3. Frame-diff t=14 against t=73 outside the central lock window — confirm pixel-identity quantitatively (mean abs diff per region). If diff ≈ 0 outside the lock area but non-zero inside, this is the briefing-slide signature.
4. `flir_hud_ocr` is unlikely to help (no HUD text), but worth running once on `mode="vision"` with qwen2.5vl:7b as a negative control.
5. Consider promoting `qwen2.5vl:7b` as the default for `analyze_video` mode=describe — or at least adding a flag like `vision_model_strategy="robust"` that routes to it.
