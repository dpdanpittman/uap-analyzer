# Deploy runbook — uap-analyzer

## Target

- **Host**: zaphod (`192.168.6.56`), SSH port 2222, user `zaphod-beeblebox`.
- **Container port**: 3260 (host-networked so the container can reach `127.0.0.1:11434` ollama).
- **Data dir**: `/srv/uap-data/` on zaphod. Bind-mounted into the container.
- **Cache dir**: `/srv/uap-data/.cache/` (frames, SQLite index, OCR text).

## First deploy

From your laptop:

```bash
./deploy/zaphod-bootstrap.sh
```

This will:

1. `ssh` to zaphod, create `/srv/uap-data/` and chown it to `zaphod-beeblebox`.
2. `rsync` videos from `~/Downloads/uapvideos/*.mp4` to `/srv/uap-data/videos/`.
3. `rsync` `Release_1.zip`, unzip in place to `/srv/uap-data/Release_1/`.
4. `rsync` the source repo to `/home/zaphod-beeblebox/uap-analyzer/`.
5. `docker compose up -d --build` to start the container.

Total: ~2.5 GB of data over the LAN. First run will take a few minutes.

## Subsequent code-only deploys

Prefer the idempotent code-only path:

```bash
./deploy/zaphod-deploy.sh              # rsync + build + restart + healthz check
./deploy/zaphod-deploy.sh --no-build   # source-only push (no rebuild)
./deploy/zaphod-deploy.sh --no-restart # build but leave the running container
```

`zaphod-deploy.sh` is the canonical redeploy entrypoint. It pins the rsync
exclude set so `.env`, `node_modules`, `dist`, `.git`, and `__pycache__`
never get pushed or deleted on the remote. **Do not** use a plain
`rsync -aP --delete` against `~/src/uap-analyzer/` on zaphod — it will
wipe the deploy-host `.env` (gitignored locally so it's not in the source
tree) and the container will silently fall back to mounting an empty
`/srv/uap-data` (regression learned 2026-05-22).

The legacy `zaphod-bootstrap.sh --skip-corpus` still works but is less safe;
`zaphod-deploy.sh` is preferred.

## Verify

```bash
# Liveness from your laptop
curl -sS http://192.168.6.56:3260/healthz | jq

# Should show:
# {
#   "status": "ok",
#   "data_dir": "/srv/uap-data",
#   "ollama_host": "http://192.168.6.56:11434",
#   "vision_model": "qwen2.5vl:7b",
#   "corpus_items": 290
# }

# Container logs on zaphod
ssh -p 2222 zaphod-beeblebox@192.168.6.56 'docker logs uap-analyzer --tail 50'
```

## Register with Claude Code

```bash
claude mcp add --transport http uap-analyzer http://192.168.6.56:3260/mcp
```

## Models on the host ollama

uap-analyzer uses three models, all served by the native ollama daemon on
zaphod at `http://localhost:11434`:

| Env var               | Default        | Used by                                            |
| --------------------- | -------------- | -------------------------------------------------- |
| `OLLAMA_TEXT_MODEL`   | `qwq:32b`      | `analyze_pdf(mode="summary")`                      |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:7b` | `describe_image`, `analyze_video(mode="describe")` |
| `OLLAMA_HUD_MODEL`    | `qwen2.5vl:7b` | `flir_hud_ocr(mode="vision")`                      |

> v0.4.3 flipped `OLLAMA_VISION_MODEL` from `llama3.2-vision:11b` to
> `qwen2.5vl:7b`. `llama3.2-vision:11b` remains in the
> `VALID_VISION_MODELS` whitelist if you want to A/B via the `model`
> override on `describe_image` or `OLLAMA_VISION_MODEL` in `.env`.

Pull a model via the HTTP API (the ollama CLI may not be on the deploy-user PATH):

```bash
ssh zaphod-beeblebox@192.168.6.56 \
  'curl -sN -X POST http://localhost:11434/api/pull \
     -H "Content-Type: application/json" \
     -d "{\"model\":\"qwen2.5vl:7b\",\"stream\":true}" \
   | grep status'
```

Verify a model is loaded:

```bash
ssh zaphod-beeblebox@192.168.6.56 \
  'curl -fsS http://localhost:11434/api/tags' | jq '.models[].name'
```

## Tear down

```bash
ssh -p 2222 zaphod-beeblebox@192.168.6.56 \
  'cd /home/zaphod-beeblebox/uap-analyzer && docker compose down'
```

Note: the corpus at `/srv/uap-data/` is preserved.

## Troubleshooting

- **container can't reach ollama**: `network_mode: host` in `docker-compose.yml` makes the container share zaphod's network namespace, so `http://localhost:11434` works. If you change that to bridge mode, switch `OLLAMA_HOST` to `http://host.docker.internal:11434` and add the `extra_hosts` mapping.
- **/healthz returns 503 / 500**: check `docker logs uap-analyzer` — most likely `UAP_DATA_DIR` doesn't exist or isn't readable inside the container.
- **corpus shows 0 items**: the initial scan happens on startup; if the data dir was empty when the container started, restart it after rsyncing.
- **MCP register fails**: confirm the URL is reachable from where you're running `claude` (laptop). The container is host-networked so `192.168.6.56:3260` works LAN-wide.
