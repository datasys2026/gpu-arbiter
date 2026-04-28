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
- Clear errors: `gpu_busy`, `insufficient_vram`, `upstream_error`
- Health and model list endpoints

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
