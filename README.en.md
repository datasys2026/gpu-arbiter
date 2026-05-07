# GPU Arbiter

GPU Arbiter is a lightweight reverse proxy for single-machine Docker Compose AI stacks.
It serializes GPU jobs via a global in-memory lock, polls for available VRAM after running
unload hooks, checks service health, then forwards the request to the upstream model service.

Target: a single 3090/4090-class machine running heterogeneous local services (image, speech,
music, LLM chat). Not a replacement for LiteLLM, Triton, KServe, or GPUStack.

## Languages

- English: this file
- Traditional Chinese: [README.md](./README.md)

---

## Request flow (GPU routes)

For every configured GPU route, GPU Arbiter applies this sequence in order:

1. **Resolve model** — from the request body `model` field or the route path.
2. **Acquire GPU lock** — in-memory; concurrent requests queue here.
3. **Run `unload` hooks** — HTTP hooks that tell other services to free VRAM. Best-effort (errors ignored).
4. **Poll for VRAM** — reads free GPU memory via NVML every 2 s, up to 60 s. Returns `503` on timeout.
5. **Run `health` hook** — waits for the upstream to be ready (optional per model).
6. **Proxy request** — forwards the original request to the upstream service.
7. **Cooldown** — optional sleep before releasing the lock (prevents back-to-back hammering).

Non-GPU routes (`uses_gpu: false`) skip steps 2–5 and proxy directly.

---

## API endpoints

### `GET /health`

Returns arbiter status and current GPU state.

```json
{
  "status": "ok",
  "gpu": { "index": 0, "free_mb": 22000 },
  "models": ["local/image-turbo", "local/tts"],
  "holder": null
}
```

`holder` is the model ID currently holding the GPU lock, or `null` if free.

---

### `GET /models`

Returns a list of all configured model IDs.

```json
{ "data": [{ "id": "local/image-turbo" }, { "id": "local/tts" }] }
```

---

### `POST /admin/unload`

Fires all `unload` hooks for all models. Use to drain the GPU before maintenance or before
switching to a model with large VRAM requirements.

**Success:**
```json
{ "status": "ok" }
```

**GPU busy (409):**
```json
{
  "error": {
    "type": "gpu_busy",
    "message": "GPU is occupied by another generation job",
    "retryable": true,
    "holder": "local/image-turbo"
  }
}
```

---

### `POST|GET /<any-path>` — Model proxy

All other paths are routed to the matching model's upstream. The model is resolved from:
1. The request body JSON field `"model"` (e.g. `{"model": "local/image-turbo", ...}`)
2. The route path alone (when only one model matches that path)

**Example — image generation:**
```
POST /v1/images/generations
Authorization: Bearer <token>
Content-Type: application/json

{ "model": "local/image-turbo", "prompt": "...", ... }
```

**Success:** upstream response forwarded as-is (status + body + headers).

---

## Error responses

All errors follow the same envelope:

```json
{
  "error": {
    "type": "<error_type>",
    "message": "<human readable>",
    "retryable": true | false,
    ...extra fields...
  }
}
```

| HTTP | `type` | Meaning | Retryable |
|------|--------|---------|-----------|
| 409 | `gpu_busy` | Another request holds the GPU lock | ✅ |
| 503 | `insufficient_vram` | Not enough free VRAM after 60 s of polling | ✅ |
| 404 | `model_not_found` | No model matches the route or `model` field | ❌ |
| 502 | `upstream_error` | Upstream returned non-2xx | ✅ |

`insufficient_vram` includes `free_mb` and `required_mb` fields.
`gpu_busy` includes `holder` (the model ID currently using the GPU).

---

## Config format

```yaml
gpu:
  index: 0               # GPU device index (default: 0)
  cooldown_seconds: 2    # sleep after each request before releasing lock

models:
  <model-id>:
    route: /v1/images/generations   # URL path this model handles
    upstream: http://image-api:8003 # where to forward the request
    uses_gpu: true                  # false = skip lock/VRAM check (default: true)
    required_vram_mb: 12000         # minimum free VRAM required

    health:                         # optional: wait for upstream readiness
      type: http
      url: http://image-api:8003/health
      method: GET
      wait_timeout_seconds: 180

    unload:                         # optional: hooks to free VRAM before this model runs
      - type: http
        url: http://other-api:8002/admin/unload
        timeout_seconds: 30
        headers:
          Authorization: Bearer ${OTHER_API_KEY}
      - type: http
        url: http://ollama:11434/api/generate
        body_json:
          model: llama3:8b
          keep_alive: 0
```

Multiple models can share the same `route` — the `model` field in the request body selects
which config applies. If only one model matches the route, the `model` field is optional.

`x-` prefixed top-level keys are ignored (use them for YAML anchors).

Environment variables are expanded in all string values: `${VAR_NAME}`.

---

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test,nvml]"
pytest
gpu-arbiter --config examples/config.example.yaml --host 0.0.0.0 --port 8090
```

## Deployment (Docker Compose)

```yaml
services:
  gpu-arbiter:
    image: ghcr.io/datasys2026/gpu-arbiter:latest
    ports:
      - "8090:8090"
    volumes:
      - ./config/gpu-arbiter.yaml:/config/config.yaml:ro
    devices:
      - /dev/nvidia0
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

See [`examples/docker-compose.yml`](./examples/docker-compose.yml) and
[`examples/config.example.yaml`](./examples/config.example.yaml) for a full working example.

## Docs

- [Architecture](./docs/architecture.md)
- [Configuration](./docs/configuration.md)
- [Routing](./docs/routing.md)
- [Error Codes](./docs/errors.md)
- [Compatibility](./docs/compatibility.md)
- [Traditional Chinese docs](./docs/index.zh-TW.md)
