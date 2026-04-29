# Architecture

GPU Arbiter sits in front of existing model services and acts as a narrow control plane.
It does not own the model runtimes themselves.

## Components

- HTTP API built with FastAPI
- In-memory GPU lock for single-process serialization
- VRAM probe abstraction with an NVML implementation
- Lifecycle hook runner for unload and health checks
- Reverse proxy to the selected upstream service

## Design intent

- Keep the model backends unchanged.
- Make GPU contention explicit and retryable.
- Treat unload and health as first-class lifecycle steps.
- Keep the code small enough to run in a Docker Compose stack.

## Non-goals

- Distributed scheduling
- Multi-node cluster orchestration
- Model registry management
- Automatic model placement across many GPUs

