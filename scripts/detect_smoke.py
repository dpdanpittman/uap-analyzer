"""Smoke-test detect_objects across FLIR + photo + (any) daylight material."""

import asyncio
import time

from uap_analyzer.config import Config
from uap_analyzer.corpus import Corpus
from uap_analyzer.tools.detect import detect_objects


SAMPLES = (
    # FLIR clips — expect ~0 detections; COCO has no class for IR blobs.
    ("videos/DOD_111689022.mp4", "FLIR"),
    ("videos/DOD_111689090.mp4", "FLIR"),
    # FBI photo set — expect real detections (daylight stills).
    ("Release_1/FBI-Photo-A1.png", "photo"),
    ("Release_1/FBI-Photo-A2.png", "photo"),
    ("Release_1/FBI-Photo-A3.png", "photo"),
)


async def main():
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)
    print("running detect_objects on each sample with yolov8n @ conf=0.25 ...")
    print()

    for path, kind in SAMPLES:
        t0 = time.time()
        try:
            if kind == "FLIR":
                result = await detect_objects(cfg, corpus, path, sample_count=3)
            else:
                result = await detect_objects(cfg, corpus, path)
        except FileNotFoundError:
            print(f"=== {path} ===  not in corpus, skipping")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"=== {path} ===  ERROR: {e!r}")
            continue
        dt = time.time() - t0
        print(f"=== {path}  [{kind}]  ({dt:.1f}s) ===")
        print(f"  total_detections: {result['consensus']['total_detections']}")
        print(f"  top_labels: {result['consensus']['top_labels']}")
        print(f"  by_label: {result['consensus']['by_label']}")
        for f in result["frames"]:
            t = f.get("at_seconds")
            ds = f["detections"]
            if ds:
                print(f"  t={t}s  count={len(ds)}")
                for d in ds[:4]:
                    print(f"    {d['label']:14s}  conf={d['confidence']}  bbox={d['bbox']}")
                if len(ds) > 4:
                    print(f"    ... (+{len(ds)-4} more)")
        print()


if __name__ == "__main__":
    asyncio.run(main())
