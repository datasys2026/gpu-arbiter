# Configuration

Configuration is loaded from YAML.

## Top-level keys

- `gpu.index`: GPU index passed to NVML
- `gpu.cooldown_seconds`: optional cooldown after a successful request
- `models`: mapping of model id to model configuration

## Model keys

- `route`: public route handled by the arbiter
- `upstream`: upstream base URL
- `uses_gpu`: whether the request should use GPU lock / VRAM preflight; defaults to `true`
- `required_vram_mb`: minimum free VRAM required before the request starts
- `health`: optional HTTP hook checked before proxying
- `unload`: optional HTTP hook called before loading or forwarding a request

## Environment variables

String values in the YAML file support environment variable expansion via `${NAME}` syntax.

## Examples

- [`examples/config.example.yaml`](../examples/config.example.yaml)
- [`examples/config.aiark.yaml`](../examples/config.aiark.yaml)
