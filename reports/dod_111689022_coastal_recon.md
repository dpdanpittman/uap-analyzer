# DOD_111689022 — coastal reconnaissance snippet (24s, low-res sensor)

**Date analyzed:** 2026-05-12
**Source:** `videos/DOD_111689022.mp4`
**Artifact type:** video
**Status:** initial (one frame sampled)

## TL;DR

A 24-second clip in an unusual format compared to the rest of the DOD video corpus: **800×444 resolution at 10 fps**. Frame sampled at t=19.6s shows an aerial grayscale view of an arid coastline with surf wrapping around a point of land, cyan tactical HUD overlay (cursor, "N" north-up reticle), and **four black redaction rectangles in the corners** blocking metadata overlays. No obvious UAP in the sampled frame — the cursor sits on a natural landform. Sensor type is consistent with a wide-area airborne mapper, older drone EO camera, or surveillance-style sensor — not a FLIR tracking pod.

## Metadata

- Duration: 24.5 s
- Resolution: **800×444** (1.80:1 aspect — slightly wider than 16:9)
- Frame rate: **10 fps**
- Codec: H.264, yuv420p
- Bitrate: ~786 kbps
- File size: 2.4 MB
- Container: mp4
- Audio: AAC present

The 800×444 / 10fps combo is the **single biggest format anomaly across the 28 DOD videos**, all of which are otherwise 1920×1080 at 30 fps. This is a different sensor or a heavily downsampled feed.

## Observations

Frame at `./frames/DOD_111689022_t19.6.jpg` (80% through duration):

- **Aerial grayscale view of coastline.** Land mass occupying the lower 2/3 of the frame, ocean / water in the upper 1/3. Surf line visible where water meets land.
- **Texture suggests arid environment.** Land has a stippled / patchy appearance consistent with sparse vegetation cover. No clear vegetation patterns, urban grids, or vessels visible.
- **Cyan HUD overlay:** centered crosshair / cursor in the middle of the FOV. "N" marker above and slightly left of cursor (north-up sensor orientation).
- **Four black redaction rectangles** in the corners (top-center, top-right, bottom-left, bottom-right). Smaller and fewer than the DOD_111689090 redactions. Likely blocking classification banner, location coords, sensor info, and operator info.
- **No obvious UAP** in this frame. The cursor sits on a natural feature where the surf wraps around a small headland.

## Notable / anomalous details

- The format mismatch with the rest of the DOD video corpus is the most interesting datum. Possibilities:
  - Different sensor / platform than the FLIR pods that produced the other clips.
  - Downsampled / proxy version of higher-res original.
  - Older release vintage from a different decade's equipment.
  - Native lower-res sensor — older drone EO, satellite, or a non-Navy platform (Coast Guard, Army aviation, allied partner).
- The frame doesn't show a UAP. The video could be:
  - Background / context footage attached to a different UAP incident.
  - A clip where the UAP appears at a different timestamp than the one sampled.
  - An incidental capture included for completeness.
- Vision model previously described this frame as "no objects visible" — direct inspection confirms no obvious target, but the model also missed the redactions and the "N" reticle, so the absence of an object claim should not be relied on.

## Hypotheses

(Speculative.)

1. **Wide-area mapper / context establish shot.** The low resolution and frame rate suggest a wider-FOV sensor optimized for area coverage rather than target tracking. This might be the orientation / approach shot before a target lock.
2. **Coast Guard, allied, or partner-platform feed.** Different sensor format would be consistent with non-DOD-Navy origin.
3. **The UAP is elsewhere in the clip.** Only one frame sampled — sample more to check.

## Cross-references

- None established yet. Worth checking whether any DOW or DOS report describes a coastline observation at low altitude.

## Next steps

- Sample 4–5 more frames across the 24-second duration to determine whether a UAP appears at a different timestamp.
- If still no UAP at any sampled timestamp, flag as a "context shot" or a candidate for further format investigation (what platform / sensor is this?).
- Geolocate the coastline if possible (arid + this shape) — sun angle, surf direction, and coastline silhouette might be enough for a regional match.
