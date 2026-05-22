# UAP analyzer — findings reports

Working log of individual findings on items in the UAP corpus (`/srv/uap-data/`, ~157 files across PDFs, images, and DOD videos).

## How this directory works

- **One report per artifact analyzed.** Filename = lowercased artifact ID with notes (e.g. `dod_111689090_maritime_tracking.md`).
- **Each report is self-contained.** Reader shouldn't need to chase down other files to understand what was found, though links between related reports are encouraged.
- **Frames are evidence.** When a finding cites a specific frame, the frame goes in `./frames/` with the artifact ID and timestamp embedded in the filename (`DOD_111689090_t260.jpg` = the t=260s frame from DOD_111689090.mp4).
- **No fabrication.** If the vision model said something, attribute it to the vision model. Direct frame inspection should be flagged as such. Speculation goes in clearly-marked "Hypotheses" sections.
- **Cross-reference openly.** If a finding might correlate with another document in the corpus (e.g. a video matching a DOW mission report), say so and link the corresponding report.

## Suggested report shape

```markdown
# <artifact id> — <short descriptor>

**Date analyzed:** YYYY-MM-DD
**Source:** Release_1/... or videos/...
**Artifact type:** PDF / video / image
**Status:** initial / detailed / cross-referenced

## TL;DR

One paragraph. What did we find that matters?

## Metadata

File size, duration, dimensions, codec, page count, source release, etc.

## Observations

What's actually in the artifact. Frame-by-frame or page-by-page if useful.
Cite frames or page numbers explicitly.

## Notable / anomalous details

What stands out. Redactions, format oddities, contradictions with vision-model output, etc.

## Hypotheses

Speculative interpretation. Flag as speculative.

## Cross-references

Other reports / corpus items that might relate.

## Next steps

What would deepen this finding.
```

## Roll-up

When the corpus is fully walked, a `MAIN_REPORT.md` will summarize across all findings — themes, redaction patterns, cross-cutting hypotheses, the most analytically interesting items, and what remains unexplained.

## Index

- [Western US Event slides (AARO, 2026-05-08)](western_us_event_slides.md) — federal LE special-agent encounters in restricted zone: "orbs launching orbs", large fiery orb, dark/transparent kites
- [DOD_111689090 — maritime IR tracking (4:53)](dod_111689090_maritime_tracking.md) — 5-min FLIR clip ending in ocean scene with two vessels + wake
- [DOD_111689232 — NASA logo placeholder](dod_111689232_nasa_placeholder.md) — entire 6-min "video" is the NASA meatball; evidence of redaction-by-substitution
- [DOD_111689022 — coastal recon snippet (24s)](dod_111689022_coastal_recon.md) — 800×444 / 10fps wide-area sensor over arid coastline
