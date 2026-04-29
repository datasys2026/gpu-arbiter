from __future__ import annotations

import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from gpu_arbiter.config import ArbiterConfig, ModelConfig
from gpu_arbiter.errors import error_payload
from gpu_arbiter.lifecycle import LifecycleRunner
from gpu_arbiter.locking import GPUBusyError, InMemoryGPULock
from gpu_arbiter.vram import InsufficientVRAMError, StaticVRAMProbe


def create_app(
    config: ArbiterConfig,
    *,
    gpu_lock: InMemoryGPULock | None = None,
    vram_probe: StaticVRAMProbe | None = None,
    lifecycle_runner: LifecycleRunner | None = None,
) -> FastAPI:
    app = FastAPI(title="GPU Arbiter")
    lock = gpu_lock or InMemoryGPULock()
    probe = vram_probe or StaticVRAMProbe(free_mb=10**9)
    lifecycle = lifecycle_runner or LifecycleRunner()

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "gpu": {"index": config.gpu.index, "free_mb": probe.get_free_mb()},
            "models": sorted(config.models),
            "holder": lock.holder,
        }

    @app.get("/models")
    def models() -> dict:
        return {"data": [{"id": model_id} for model_id in sorted(config.models)]}

    @app.post("/admin/unload")
    def unload_all() -> dict:
        for model in config.models.values():
            lifecycle.run_hook(model.unload)
        return {"status": "ok"}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request) -> Response:
        route = "/" + path
        body = await request.body()
        model_id = _extract_model_id(request, body)
        model = _resolve_model(config, route, model_id)
        if model is None:
            return JSONResponse(
                status_code=404,
                content=error_payload(
                    "model_not_found",
                    "No configured model matches this request",
                    False,
                    model=model_id,
                    route=route,
                ),
            )

        if not model.uses_gpu:
            try:
                lifecycle.wait_for_health(model.health)
                return await _proxy_request(model, route, request, body)
            except httpx.HTTPStatusError as exc:
                return JSONResponse(
                    status_code=502,
                    content=error_payload(
                        "upstream_error",
                        "Upstream service returned an error",
                        True,
                        upstream_status_code=exc.response.status_code,
                    ),
                )

        try:
            with lock.acquire(model_id or route):
                lifecycle.run_hook(model.unload)
                lifecycle.wait_for_health(model.health)
                probe.ensure_available(model.required_vram_mb)
                response = await _proxy_request(model, route, request, body)
                if config.gpu.cooldown_seconds:
                    time.sleep(config.gpu.cooldown_seconds)
                return response
        except GPUBusyError as exc:
            return JSONResponse(
                status_code=409,
                content=error_payload(
                    "gpu_busy",
                    "GPU is occupied by another generation job",
                    True,
                    holder=exc.holder,
                ),
            )
        except InsufficientVRAMError as exc:
            return JSONResponse(
                status_code=503,
                content=error_payload(
                    "insufficient_vram",
                    "Not enough free GPU memory",
                    True,
                    free_mb=exc.free_mb,
                    required_mb=exc.required_mb,
                ),
            )
        except httpx.HTTPStatusError as exc:
            return JSONResponse(
                status_code=502,
                content=error_payload(
                    "upstream_error",
                    "Upstream service returned an error",
                    True,
                    upstream_status_code=exc.response.status_code,
                ),
            )

    return app


def _extract_model_id(request: Request, body: bytes) -> str | None:
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type or not body:
        return None
    try:
        import json

        payload = json.loads(body)
    except Exception:
        return None
    model = payload.get("model") if isinstance(payload, dict) else None
    return model if isinstance(model, str) else None


def _resolve_model(config: ArbiterConfig, route: str, model_id: str | None) -> ModelConfig | None:
    if model_id and model_id in config.models:
        return config.models[model_id]
    route_matches = [model for model in config.models.values() if model.route == route]
    if len(route_matches) == 1:
        return route_matches[0]
    return None


async def _proxy_request(model: ModelConfig, route: str, request: Request, body: bytes) -> Response:
    upstream_url = model.upstream.rstrip("/") + route
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    async with httpx.AsyncClient(timeout=900) as client:
        upstream_response = await client.request(
            request.method,
            upstream_url,
            content=body,
            headers=headers,
            params=request.query_params,
        )
    media_type = upstream_response.headers.get("content-type")
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=media_type,
    )
