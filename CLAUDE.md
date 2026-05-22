# uap-analyzer

MCP server for analyzing UAP / DOW release material. See `README.md` for the full overview.

## How this fits in the stack

- Runs as a Docker container on **zaphod (192.168.6.56)**, port **3260**.
- Talks to **ollama at http://192.168.6.56:11434** (host-network so it can reach the host's ollama daemon).
- Reads corpus from **/srv/uap-data/** (hostPath bind mount).
- Writes cache + SQLite index to **/srv/uap-data/.cache/**.
- Registered with Claude Code via `claude mcp add --transport http uap-analyzer http://192.168.6.56:3260/mcp`.

## Conventions

- All tools must return **text or structured JSON**, not raw image/video bytes. Exception: `extract_frame` can return base64 JPEG when explicitly requested.
- All paths in tool args are **relative to UAP_DATA_DIR**. The server resolves them and refuses anything outside that root.
- Each tool call updates the SQLite cache. Future identical calls return cached results.
- Vision model calls go through `tools/ollama_client.py`. Never call ollama HTTP directly from tool handlers.

## Adding a new tool

1. Implement in `src/uap_analyzer/tools/<area>.py` as a plain async function.
2. Register in `src/uap_analyzer/server.py` via `@mcp.tool()` decorator.
3. Add a smoke test in `tests/test_smoke.py`.

## Don't

- Don't return raw video files or PDFs in responses — too big.
- Don't write outside `UAP_CACHE_DIR`.
- Don't call out to cloud APIs. All inference goes via the local ollama on zaphod.
- Don't add tools that mutate the corpus. This server is read-only against the source files.
