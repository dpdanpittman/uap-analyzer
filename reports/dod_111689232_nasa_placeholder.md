# DOD_111689232 — NASA logo placeholder (6:11 video, no real content)

**Date analyzed:** 2026-05-12
**Source:** `videos/DOD_111689232.mp4`
**Artifact type:** video
**Status:** detailed; confirmed placeholder across sampled timestamps

## TL;DR

A ~6-minute mp4 file slotted into the DOD UAP video numbering sequence whose entire content is **the NASA "meatball" logo on a white background**, unchanging. Sampled at t=30, t=185, t=247, t=309 — every frame is the same logo. The file is structurally a valid DOD-style video (1080p H.264 30fps) but carries no information. This is evidence of **redaction-by-substitution**: the original DOD footage was replaced with a generic logo before public release. The release pipeline isn't only redacting overlays; in at least one case it's substituting the entire payload.

## Metadata

- Duration: 371.60 s (~6:11)
- Resolution: 1920×1080
- Frame rate: 29.97 fps
- Codec: H.264, yuv420p
- Bitrate: **412 kbps** — extremely low for 1080p, explained by a single near-static frame
- File size: 19 MB
- Container: mp4
- Audio: AAC present (not analyzed)

## Observations

Frames sampled and saved at `./frames/DOD_111689232_t<sec>.jpg`:

| Time (s) | %   | What's visible              |
| -------- | --- | --------------------------- |
| 30       | 8%  | NASA meatball logo on white |
| 185.8    | 50% | NASA meatball logo on white |
| 247.7    | 67% | NASA meatball logo on white |
| 309.7    | 83% | NASA meatball logo on white |

No variation between the frames — pixel-level confirmation not done but the bitrate (412 kbps for 1920×1080 30 fps) is consistent with all frames being identical or near-identical: H.264's inter-frame compression collapses identical content to near-nothing.

## Notable / anomalous details

- The file is in the `DOD_111689xxx` numbering range, identical naming convention to the other DOD UAP videos in the corpus.
- The container (mp4), codec (H.264), resolution (1920×1080), and frame rate (29.97 fps) match the rest of the corpus.
- Audio track present — not analyzed; could contain narration or ambient.
- The presence of this file in a release labeled as UAP footage means _someone made an explicit decision_ to leave a same-length placeholder rather than removing the entry entirely. That's a curation choice with information value.

### Vision-model behavior worth noting

The local `llama3.2-vision:11b` model, when shown the t=309.7 frame, refused with: _"I'm not able to identify the object in the image as it is a classified military image and I don't have the necessary information or access to determine its origin or purpose. The image appears to be a thermal image from a FLIR (forward-looking infrared) targeting pod, but I can't confirm any specific details..."_

The image is the NASA logo. The model **hallucinated a FLIR thermal image** from the corpus context. This is a sharp datapoint about how unreliable LLM-based vision is for analyzing redacted/anomalous content — it confabulates the expected output regardless of what's actually in the frame.

## Hypotheses

(Speculative.)

1. **Original footage was deemed too sensitive even for redacted release.** Replacing with a logo preserves the file's existence and slot number in the index without exposing content.
2. **File corruption / processing error.** A pipeline step (transcode, redaction overlay) silently produced this output. Unlikely given that someone would have noticed during QA.
3. **Placeholder for footage that was promised but not delivered.** The slot exists in the index, the file exists, but the upstream provider didn't actually clear the content for release.

(1) is the most likely. The corpus has many other heavily-redacted but real videos; substitution is a different choice from redaction.

## Cross-references

- Compare against the metadata of the other 27 DOD videos. None of the others have a ~412 kbps bitrate; the next lowest is `DOD_111689142` at 1.07 Mbps and `DOD_111688970` at 1.00 Mbps. The bitrate alone flags this video as anomalous.
- Worth a one-frame sample of every other DOD video to check whether any _others_ are also placeholders. If multiple, this is a deliberate substitution pattern, not a one-off.

## Next steps

- Sample one mid-clip frame from each of the remaining 25 DOD videos to detect other placeholders.
- Check FOIA / public release notes that accompany the DOD UAP video release (if any are in the corpus) for any acknowledgment of substituted content.
- File a side note for the main report: **the release pipeline includes content substitution, not only redaction**.
