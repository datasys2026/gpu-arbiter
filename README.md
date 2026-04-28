# GPU Arbiter

GPU Arbiter is a lightweight runtime controller for single-machine Docker Compose AI stacks.
It serializes heavy GPU jobs, checks available VRAM, runs model lifecycle hooks, proxies
requests to existing model services, and returns clear retryable errors.

It is not a replacement for LiteLLM, Triton, KServe, or GPUStack. The first target is a
single 3090/4090-class machine running a few heterogeneous local services such as image,
speech, and music generation backends.

## First Scope

- Global GPU lock
- Model-to-upstream routing
- NVML-compatible VRAM preflight interface
- HTTP lifecycle hooks
- Unload-before-load execution order
- Clear errors: `gpu_busy`, `insufficient_vram`, `upstream_error`
- Health and model list endpoints

## Request Flow

For every configured GPU route, GPU Arbiter applies the same sequence:

1. Resolve the target model from the request body `model` field or route.
2. Acquire the global GPU lock.
3. Run the model's `unload` hook when configured.
4. Wait for the model's `health` hook when configured.
5. Check free VRAM against `required_vram_mb`.
6. Proxy the request to the upstream service.
7. Apply optional cooldown before releasing the lock.

This keeps lifecycle management outside individual model services while preserving their
existing APIs.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test,nvml]"
pytest
```

## Example

```bash
gpu-arbiter --config examples/config.aiark.yaml --host 0.0.0.0 --port 8090
```

The example config demonstrates AIARK-style image, TTS, and music routes. Hooks are plain
HTTP calls, so model services can expose lightweight `/admin/unload` endpoints or use an
adapter service that restarts containers.
