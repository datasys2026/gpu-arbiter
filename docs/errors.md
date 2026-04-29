# Error Codes

GPU Arbiter returns structured retryable errors.

## `gpu_busy`

The GPU is already occupied by another job.

Typical status code: `409`

## `insufficient_vram`

The configured model requires more free VRAM than is currently available.

Typical status code: `503`

## `upstream_error`

The upstream service returned a non-2xx error while the arbiter was proxying the request.

Typical status code: `502`

## `model_not_found`

No configured model matches the request route or `model` field.

Typical status code: `404`

