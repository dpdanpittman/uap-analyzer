"""Compare flir_hud_ocr ocr-mode vs vision-mode on candidate FLIR videos."""

import asyncio
import time

from uap_analyzer.config import Config
from uap_analyzer.corpus import Corpus
from uap_analyzer.tools.flir import flir_hud_ocr


CANDIDATES = ("111689022", "111689090", "111688723", "111688762", "111688775")


async def main():
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)
    items = corpus.list(kind="video")
    targets = []
    for needle in CANDIDATES:
        m = next((i for i in items if needle in i["path"]), None)
        if m:
            targets.append(m["path"])

    print(f"HUD model: {cfg.ollama_hud_model}")
    print(f"trying {len(targets)} candidate videos in vision mode\n")

    for path in targets:
        t0 = time.time()
        try:
            result = await flir_hud_ocr(
                cfg, corpus, path, mode="vision", sample_count=2, width=1280
            )
        except Exception as e:  # noqa: BLE001
            print(f"=== {path} === ERROR: {e!r}")
            continue
        dt = time.time() - t0
        print(f"=== {path} ({dt:.1f}s) ===")
        print(f"  fields_observed: {result['fields_observed']}")
        print(f"  consensus: {result['consensus']}")
        for f in result["frames"]:
            print(f"  t={f['at_seconds']}s  fields={f['fields']}")
            if f.get("raw_text"):
                print(f"    raw: {f['raw_text']!r}")
            if f.get("error"):
                print(f"    ERROR: {f['error']}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
