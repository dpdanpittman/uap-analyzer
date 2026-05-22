# DOD_111689090 — maritime IR tracking, ~5 minutes

**Date analyzed:** 2026-05-12
**Source:** `videos/DOD_111689090.mp4`
**Artifact type:** video
**Status:** detailed (10 frames sampled across duration)

## TL;DR

4:53 of FLIR/IR tracking footage. Cursor stays locked at center of FOV throughout while the "N" north-up reticle drifts across the frame — the sensor is rotating on a gimbal to keep something centered. For roughly the first 4 minutes the FOV is filled with dense gray cloud or fog (IR sees through it). At t≈260s the cloud clears to reveal **two distinct objects on the ocean surface with a wake/trail connecting them**. By t=285s only one vessel remains visible. This is consistent with a maritime UAP / trans-medium event captured by a Navy or DOD ISR platform. HUD is heavily redacted throughout — ~8 black rectangles cover the metadata overlays (altitude, range, bearing, sensor mode, classification, tail, AOR).

## Metadata

- Duration: 293.17 s (~4:53)
- Resolution: 1920×1080
- Frame rate: 30.303 fps (slightly above 30, NTSC drop-frame style)
- Codec: H.264, yuv420p
- Bitrate: ~7.5 Mbps (highest-detail long video in the corpus)
- File size: 275 MB
- Container: mp4 (mov,mp4,m4a,3gp,3g2,mj2)
- Audio: AAC present

## Observations (frame-by-frame)

All frames cached at `./frames/DOD_111689090_t<sec>.jpg`. Width 1280–1600 px.

| Time (s) | %   | What's visible                                                                                                                                                                                                                                 |
| -------- | --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 15       | 5%  | Dense gray cloud / fog field. Small white pinpoint near center-left. N reticle in lower-middle. Cursor centered.                                                                                                                               |
| 45       | 15% | Darker cloud / thicker region. Small white pinpoint right of center. N reticle near top center.                                                                                                                                                |
| 50       | 17% | The "money" frame already cited — diffuse cloud-like IR signature around the tracked point. Centered crosshair. Small dot at center-right.                                                                                                     |
| 75       | 26% | Cloud thinning. Cursor still centered. N has moved to upper-right. Pinpoint faintly visible center-left.                                                                                                                                       |
| 105      | 36% | (saved but not viewed in detail; same gray cloud field)                                                                                                                                                                                        |
| 165      | 56% | Cloud continues. Pinpoint faint near center. N now further right.                                                                                                                                                                              |
| 230      | 78% | Cloud thinning further, transitioning toward open sky/sea. Faint pinpoint. N mid-left.                                                                                                                                                         |
| 260      | 89% | **Reveal frame.** Open ocean with wave texture. Small dark vessel/object lower-left with what appears to be a structure or person on it. Large elongated dark object center-right. **Visible wake/trail between them.** N reticle middle-left. |
| 285      | 97% | Single vessel visible upper-center-left on water. Wake gone. Wave texture clear.                                                                                                                                                               |

The cursor remains centered in every sampled frame. The N reticle's position relative to the center documents the sensor's pan/tilt rotation across the 5 minutes.

## Notable / anomalous details

### Heavy and consistent HUD redaction

In every frame, the same ~8 black rectangles cover the same regions:

- Top center: large "T"- or "I"-shaped block (probably range/altitude/sensor mode)
- Top right: rectangle (probably classification banner or location coord)
- Left side: 3–4 stacked rectangles (sensor controls / radar return data)
- Bottom right: 1–2 rectangles (tail number or AOR designator)

The fact that the same regions are redacted across the whole video indicates a frame-mask was applied post-capture during release prep, not that the operator was hiding individual pieces of info.

### Cursor-locked tracking through opaque cloud

IR can penetrate cloud and fog where visible light cannot. The fact that the cursor stays centered for 4+ minutes while the sensor rotates on its gimbal to follow something is consistent with the operator (or auto-track) keeping a real target locked. By the time the cloud clears, the locked point coincides with the maritime scene — meaning the IR signature being tracked through the cloud was probably _something visible at the end_.

### Wake/trail at t=260s

The wake between the two visible objects is the highest-information detail in the whole clip. It implies either:

- One object created the wake and was photographed mid-motion,
- A third object (UAP) traveled across the water between the two visible objects, leaving a momentary trail,
- The "wake" is in fact a thermal artifact (a colder/warmer surface streak) rather than physical water disturbance.

### Vision model behavior

The local `llama3.2-vision:11b` model was unreliable on these frames. It described a "red target box" and "range/bearing indicator" in the t=146.59 frame neither of which is visible to direct inspection — those are typical FLIR HUD elements the model appears to be **hallucinating from training data** about famous UAP releases like Gimbal/Go Fast. The model also missed the 8 redaction rectangles in every frame and refused one frame ("classified military image") that was in fact unremarkable.

**Direct frame inspection is required** — vision model output for this corpus should be treated as a hint at best.

## Hypotheses

(Speculative.)

1. **Maritime trans-medium event.** Small object emerges from / enters water near a surface vessel. The "wake" at t=260 is the actual evidence of motion. Pattern matches the well-known DHS Aguadilla, Puerto Rico thermal video (2013).
2. **Surveillance of a small craft being pursued.** The lower-left dark blob could be a small vessel (fast attack craft, skiff) and the larger elongated object could be a barge, container, or pursuit vessel; the wake is one of them moving. This would be a conventional ISR clip miscategorized into the UAP corpus — but the heavy redaction argues against that read.
3. **Iranian fast-boat / Strait of Hormuz incident.** Several DOW reports already in the corpus describe Strait of Hormuz UAP encounters in 2020. The visuals match what would be recorded by a P-8 Poseidon or MQ-9 Reaper during such an event.

## Cross-references

- The corpus's DOW mission reports for the **Arabian Gulf, Persian Gulf, Strait of Hormuz, and Gulf of Aden in 2020** are obvious candidates for matching this video's location and context: D3, D4, D5, D6, D7 (Arabian Gulf 2020), D38 (Range Fouler Middle East May 2020), D44 (Range Fouler Arabian Sea Oct 2020), D56 (Range Fouler Arabian Sea Aug 2020), D57 (Gulf of Aden Sept 2020), D58 (Range Fouler Oct 2020), D60/D61 (Persian Gulf Aug 2020), D62/D63 (Strait of Hormuz Sept/Oct 2020), D64 (Iran Nov 2020), D65 (Persian Gulf July 2020).
- Aviation platform candidates: Navy P-8 Poseidon (typical maritime ISR), MQ-9 Reaper (drone IR pod), MH-60 Romeo (helicopter). FLIR pod model probably MX-15 / MX-20 (L3Harris WESCAM) based on HUD layout.

## Next steps

- Frame-by-frame sample of the t=255–285 window at 1-second intervals (~30 frames) to reconstruct the motion of the wake and identify what caused it.
- Cross-search the DOW mission reports (already FTS-indexed) for terms like "wake", "two vessels", "small surface contact", "dhow", "P-8", "FLIR" + the 2020 date range, and try to match this video to a written report.
- Check whether `USPER-Statement-Redacted.pdf` has any maritime references (its earlier FTS hit on "orb under FLIR" suggests it might be cross-domain).
- Sample the same time slices in the other long videos (DOD_111689115, DOD_111688825, DOD_111689011, DOD_111689083) to look for similar maritime patterns.
