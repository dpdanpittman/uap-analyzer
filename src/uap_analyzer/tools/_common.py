"""Shared utilities used across tools — cache helpers + vision-model whitelist.

Pulled out in v0.4.2 to address tribunal adversary findings A-007 (cache
version drift between flir.py at v3 and audio.py/detect.py at v2) and A-008
(`_hash_key` duplicated and diverged across three files), plus A-003
(structurally identical model-whitelist pattern needed in image.py too).

Centralizing here makes the per-tool cache-key implementation a single line
plus a tuple; future schema bumps move only one constant.
"""

from __future__ import annotations

import hashlib
from typing import Any


# Bump this constant when the cache-key shape changes across the codebase.
# All three new tools (and image.py via `describe_image`) prefix their keys
# with this string so an operator who notices stale derivatives can verify
# the version line and run `corpus.put_cached` invalidation deliberately.
#
# v0.4.0 = "v1"
# v0.4.1 = "v2" on audio/detect, "v3" on flir (the drift the adversary caught)
# v0.4.2 = "v4" everywhere (unified)
CACHE_VERSION = "v4"


def hash_key(*parts: Any) -> str:
    """Hash the params tuple into a stable cache-key fragment.

    Replaces the previous '|'-separated concatenation which was vulnerable to
    collision spoofing across distinct param tuples that string-equal once
    joined (P-uap-v04 sec-F-sec-005). Hashes the tuple via sha256 and takes
    the first 16 hex chars; that's 64 bits of collision resistance — enough
    for the corpus sizes uap-analyzer targets (low thousands of items per
    tool, low hundreds of distinct cache-key tuples per item).
    """
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# Whitelist of vision-capable ollama models the toolchain knows how to use.
# Used by both `flir_hud_ocr(mode="vision")` and `describe_image`. The list
# is intentionally narrow — adding a model means we've verified it accepts
# the prompt shapes the tools use and the response normalizer handles its
# output. (P-uap-v04 sec-F-sec-002 + P-uap-v041 adversary A-003.)
VALID_VISION_MODELS = frozenset({
    "qwen2.5vl:7b",
    "qwen2.5vl:32b",
    "qwen2.5vl:72b",
    "qwen2-vl:7b",
    "llama3.2-vision:11b",
    "llama3.2-vision:90b",
    "minicpm-v:8b",
})
