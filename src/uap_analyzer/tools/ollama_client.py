"""Async wrapper around the ollama HTTP API.

Used by:
  - describe_image (vision model)
  - analyze_pdf(mode=summary) (text model)
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import httpx

from ..config import Config

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        # Split the timeout: a tight connect+pool timeout fails fast if the
        # ollama daemon is down, while the read timeout stays generous so a
        # legitimate long-running model call (qwq:32b on a 90-min transcript,
        # vision-mode FLIR HUD on a high-zoom frame) doesn't get cut off. The
        # previous bare-int timeout was applied uniformly to all four phases,
        # turning a daemon outage into a 5-min hang per call (×N frames =
        # 25min vision-mode FLIR hang). (Tribunal perf-F-perf-005.)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,
                read=cfg.ollama_timeout,
                write=10.0,
                pool=10.0,
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.cfg.ollama_host}/api/chat"
        body: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            # ollama's "format": "json" constrains decoding to a valid JSON object.
            body["format"] = "json"
        resp = await self._client.post(url, json=body)
        if resp.status_code != 200:
            raise OllamaError(f"ollama {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        return {
            "content": data.get("message", {}).get("content", ""),
            "model": data.get("model"),
            "eval_count": data.get("eval_count"),
            "total_duration_s": (data.get("total_duration") or 0) / 1e9,
        }

    async def describe_image(
        self,
        image_path: Path,
        prompt: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 768,
        model: str | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        return await self.chat(
            model=model or self.cfg.ollama_vision_model,
            messages=[{"role": "user", "content": prompt, "images": [b64]}],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    async def text_chat(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        model: str | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(
            model=model or self.cfg.ollama_text_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
