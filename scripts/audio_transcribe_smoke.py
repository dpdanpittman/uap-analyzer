"""Smoke-test transcribe_audio across a short clip + a medium briefing."""

import asyncio
import time

from uap_analyzer.config import Config
from uap_analyzer.corpus import Corpus
from uap_analyzer.tools.audio import transcribe_audio


SAMPLES = (
    "videos/DOD_111689057.mp4",  # has real audio (max -37dB)
    "videos/DOD_111689030.mp4",  # has real audio (max -36dB)
)


async def main():
    cfg = Config.from_env()
    corpus = Corpus(cfg.data_dir, cfg.cache_dir)
    print(f"whisper_model={cfg.whisper_model}  compute_type={cfg.whisper_compute_type}")
    print()

    for sample in SAMPLES:
        print(f"=== {sample} ===")
        t0 = time.time()
        try:
            # vad_filter=False — needed for quiet declassified audio that
            # VAD aggressively flags as non-speech.
            result = await transcribe_audio(cfg, corpus, sample, vad_filter=False)
        except FileNotFoundError:
            print(f"  not in corpus, skipping")
            continue
        dt = time.time() - t0
        print(f"  took {dt:.1f}s")
        print(
            f"  language={result['language']} (p={result['language_probability']})"
            f"  duration={result['duration_s']}s  segments={result['segment_count']}"
        )
        text = result["full_text"]
        if len(text) > 600:
            print(f"  full_text ({len(text)} chars):")
            print(f"    {text[:300]}")
            print(f"    ... [truncated] ...")
            print(f"    {text[-300:]}")
        else:
            print(f"  full_text:")
            for line in text.splitlines():
                print(f"    {line}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
