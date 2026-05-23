"""Entry point: run the MCP server over HTTP/SSE on (host, port).

Attaches a `/healthz` route via FastMCP's custom_route so docker healthchecks
and external pokes can verify liveness without an MCP client.
"""

from __future__ import annotations

import logging
import sys

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse

from .server import build_server


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def build_app():
    mcp, cfg, corpus = build_server()
    _setup_logging(cfg.log_level)
    log = logging.getLogger("uap_analyzer.main")

    # On startup, do an initial corpus scan (fast — only hashes first+last 1MB).
    log.info("scanning corpus at %s ...", cfg.data_dir)
    summary = corpus.scan()
    log.info("scan complete: %s", summary)

    # Attach /healthz directly onto the FastMCP app via custom_route.
    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request):
        return JSONResponse(
            {
                "status": "ok",
                "data_dir": str(cfg.data_dir),
                "cache_dir": str(cfg.cache_dir),
                "ollama_host": cfg.ollama_host,
                "vision_model": cfg.ollama_vision_model,
                "hud_model": cfg.ollama_hud_model,
                "text_model": cfg.ollama_text_model,
                "whisper_model": cfg.whisper_model,
                "whisper_compute_type": cfg.whisper_compute_type,
                "corpus_items": len(corpus.list()),
            }
        )

    # streamable_http_app() returns a Starlette app with the MCP endpoint at
    # FastMCP's settings.streamable_http_path (default "/mcp") plus our custom
    # routes (e.g. /healthz). Serve it directly.
    if hasattr(mcp, "streamable_http_app"):
        app = mcp.streamable_http_app()
    elif hasattr(mcp, "sse_app"):
        app = mcp.sse_app()
    else:
        raise RuntimeError(
            "FastMCP has neither streamable_http_app() nor sse_app(); "
            "check installed `mcp` package version."
        )

    return app, cfg.host, cfg.port


def main() -> int:
    app, host, port = build_app()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
