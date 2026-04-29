# GPU Arbiter

GPU Arbiter is a lightweight runtime controller for single-machine Docker Compose AI stacks.
It serializes heavy GPU jobs, checks available VRAM, runs lifecycle hooks, proxies requests to
existing model services, and returns clear retryable errors.

It is not a replacement for LiteLLM, Triton, KServe, or GPUStack. The target is a single
3090/4090-class machine running a few heterogeneous local services such as image, speech,
and music generation backends.

## Languages

- English: this file
- Traditional Chinese: [README.md](./README.md)

## What it solves

- Global GPU lock across heterogeneous model services
- Model-to-upstream routing
- NVML-compatible VRAM preflight
- HTTP lifecycle hooks for unload and health
- Unload-before-load execution order
- Clear errors: `gpu_busy`, `insufficient_vram`, `upstream_error`
- Health and model list endpoints

## Request flow

For every configured GPU route, GPU Arbiter applies the same sequence:

1. Resolve the target model from the request body `model` field or the route.
2. Acquire the global GPU lock.
3. Run the model `unload` hook when configured.
4. Wait for the model `health` hook when configured.
5. Check free VRAM against `required_vram_mb`.
6. Proxy the request to the upstream service.
7. Apply optional cooldown before releasing the lock.

This keeps lifecycle management outside individual model services while preserving their
existing APIs.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test,nvml]"
pytest
gpu-arbiter --config examples/config.example.yaml --host 127.0.0.1 --port 8090
```

## Example config

Use [`examples/config.example.yaml`](./examples/config.example.yaml) as the starting point for
your own deployment. For the internal AIARK deployment sample, see
[`examples/config.aiark.yaml`](./examples/config.aiark.yaml).

## Docs

- [Architecture](./docs/architecture.md)
- [Configuration](./docs/configuration.md)
- [Routing](./docs/routing.md)
- [Error Codes](./docs/errors.md)
- [Compatibility](./docs/compatibility.md)
- [Traditional Chinese docs index](./docs/index.zh-TW.md)

## Why this repo exists

The project is intentionally smaller than a full model serving platform. The design goal is to
wrap an existing collection of model backends with a single, explicit control plane for GPU
locking, lifecycle hooks, and retryable error reporting.
