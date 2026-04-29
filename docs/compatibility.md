# Compatibility

GPU Arbiter is designed for a single-machine Docker Compose deployment.

## Good fits

- Local image, TTS, and music backends
- Existing services that already expose HTTP APIs
- Single-GPU machines where only one heavy job should run at a time

## Not a fit

- Cluster schedulers
- Multi-tenant model hosting platforms
- Highly concurrent inference fleets
- Systems that require automatic bin-packing across many GPUs

## Related tools

- LiteLLM for OpenAI-compatible text routing
- GPUStack for broader model hosting
- KServe for Kubernetes-first inference platforms
- Triton for high-performance model serving

