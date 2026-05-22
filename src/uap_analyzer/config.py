"""Environment-driven config. Loaded once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    cache_dir: Path
    ollama_host: str
    ollama_text_model: str
    ollama_vision_model: str
    ollama_timeout: int
    host: str
    port: int
    log_level: str

    @classmethod
    def from_env(cls) -> Config:
        data_dir = Path(os.environ.get("UAP_DATA_DIR", "/srv/uap-data")).resolve()
        cache_dir = Path(
            os.environ.get("UAP_CACHE_DIR", str(data_dir / ".cache"))
        ).resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            data_dir=data_dir,
            cache_dir=cache_dir,
            ollama_host=os.environ.get("OLLAMA_HOST", "http://192.168.6.56:11434"),
            ollama_text_model=os.environ.get("OLLAMA_TEXT_MODEL", "qwq:32b"),
            ollama_vision_model=os.environ.get("OLLAMA_VISION_MODEL", "llama3.2-vision:11b"),
            ollama_timeout=int(os.environ.get("OLLAMA_TIMEOUT", "300")),
            host=os.environ.get("UAP_HOST", "0.0.0.0"),
            port=int(os.environ.get("UAP_PORT", "3260")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )

    def resolve_corpus_path(self, rel_or_abs: str) -> Path:
        """Resolve a user-supplied path, rejecting anything outside data_dir.

        Accepts either an absolute path under data_dir, or a path relative to data_dir.
        """
        p = Path(rel_or_abs)
        if not p.is_absolute():
            p = self.data_dir / p
        p = p.resolve()
        try:
            p.relative_to(self.data_dir)
        except ValueError as e:
            raise ValueError(
                f"path {rel_or_abs!r} resolves outside UAP_DATA_DIR ({self.data_dir})"
            ) from e
        return p
