from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from gpu_arbiter.config import ArbiterConfig, ModelConfig
from gpu_arbiter.errors import error_payload
from gpu_arbiter.lifecycle import LifecycleRunner
from gpu_arbiter.locking import GPUBusyError, InMemoryGPULock
from gpu_arbiter.queue.models import Task, TaskStatus
from gpu_arbiter.queue.store import InMemoryTaskStore, TaskStore
from gpu_arbiter.vram import InsufficientVRAMError, StaticVRAMProbe, wait_for_vram_available


LOGGER = logging.getLogger("gpu_arbiter")


def _log_event(event: str, **fields: object) -> None:
    LOGGER.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


def _request_id(request: Request) -> str:
    rid = request.headers.get("x-request-id")
    if rid:
        rid = "".join(c for c in rid if c.isprintable() and ord(c) < 128)[:64]
        return rid or uuid.uuid4().hex
    return uuid.uuid4().hex


_MAX_QUEUE_DEPTH = 10


def create_app(
    config: ArbiterConfig,
    *,
    gpu_lock: InMemoryGPULock | None = None,
    vram_probe: StaticVRAMProbe | None = None,
    lifecycle_runner: LifecycleRunner | None = None,
    task_store: TaskStore | None = None,
) -> FastAPI:
    app = FastAPI(title="GPU Arbiter")
    lock = gpu_lock or InMemoryGPULock()
    probe = vram_probe or StaticVRAMProbe(free_mb=0)
    lifecycle = lifecycle_runner or LifecycleRunner(logger=LOGGER)
    store: TaskStore = task_store or InMemoryTaskStore()

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
    async def unload_all(request: Request) -> Response:
        request_id = _request_id(request)
        _log_event("admin_unload_start", request_id=request_id, holder=lock.holder)
        try:
            with lock.acquire("admin-unload"):
                for model_id, model in config.models.items():
                    _log_event("admin_unload_hook_start", request_id=request_id, model_id=model_id)
                    await lifecycle.run_hooks(model.unload, ignore_errors=True)
                    _log_event("admin_unload_hook_done", request_id=request_id, model_id=model_id)
                _log_event("admin_unload_done", request_id=request_id)
                return JSONResponse({"status": "ok"})
        except GPUBusyError as exc:
            _log_event("admin_unload_busy", request_id=request_id, holder=exc.holder)
            return JSONResponse(
                status_code=409,
                content=error_payload(
                    "gpu_busy",
                    "GPU is occupied by another generation job",
                    True,
                    holder=exc.holder,
                ),
            )

    @app.post("/queue", status_code=202)
    async def queue_submit(request: Request) -> Response:
        tenant_id = request.headers.get("x-tenant-id", "").strip()
        if not tenant_id:
            return JSONResponse(
                status_code=400,
                content=error_payload("missing_tenant", "X-Tenant-ID header is required", False),
            )
        body = await request.body()
        model_id = _extract_model_id(request, body)
        route = "/queue"
        model = _resolve_model(config, route, model_id) if model_id else None
        if model is None and model_id:
            model = config.models.get(model_id)
        if model is None:
            return JSONResponse(
                status_code=404,
                content=error_payload(
                    "model_not_found", "No configured model matches this request", False, model=model_id
                ),
            )
        depth = await store.queue_depth(tenant_id)
        if depth >= _MAX_QUEUE_DEPTH:
            return JSONResponse(
                status_code=429,
                content=error_payload(
                    "queue_full",
                    f"Queue depth limit ({_MAX_QUEUE_DEPTH}) reached for this tenant",
                    True,
                ),
            )
        task = Task(
            task_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            model_id=model_id or "",
            route=model.route,
            method=request.method,
            headers=dict(request.headers),
            body=body,
            status=TaskStatus.PENDING,
            created_at=time.monotonic(),
        )
        await store.create(task)
        return JSONResponse(status_code=202, content={"task_id": task.task_id, "status": "pending"})

    @app.get("/tasks/{task_id}")
    async def task_status(task_id: str, request: Request) -> Response:
        tenant_id = request.headers.get("x-tenant-id", "").strip()
        task = await store.get(task_id)
        if task is None or task.tenant_id != tenant_id:
            return JSONResponse(status_code=404, content={"error": "task not found"})
        result = None
        if task.status in (TaskStatus.DONE, TaskStatus.FAILED):
            result = {
                "status_code": task.result_status,
                "body": task.result_body.decode(errors="replace") if task.result_body else None,
                "headers": task.result_headers,
                "error": task.error,
            }
        return JSONResponse({"task_id": task.task_id, "status": task.status.value, "result": result})

    @app.get("/queue/status")
    async def queue_status() -> dict:
        return await store.active_counts()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request) -> Response:
        request_id = _request_id(request)
        route = "/" + path
        body = await request.body()
        model_id = _extract_model_id(request, body)
        model = _resolve_model(config, route, model_id)
        _log_event(
            "request_received",
            request_id=request_id,
            route=route,
            method=request.method,
            model_id=model_id,
        )
        if model is None:
            _log_event("request_route_missing", request_id=request_id, route=route, model_id=model_id)
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
                response = await _proxy_request(model, route, request, body)
                _log_event(
                    "request_completed",
                    request_id=request_id,
                    route=route,
                    model_id=model_id or route,
                    status_code=response.status_code,
                    uses_gpu=False,
                )
                return response
            except httpx.HTTPError as exc:
                _log_event(
                    "request_upstream_error",
                    request_id=request_id,
                    route=route,
                    model_id=model_id or route,
                    error=str(exc),
                )
                return JSONResponse(
                    status_code=502,
                    content=error_payload(
                        "upstream_error",
                        "Upstream service returned an error",
                        True,
                    ),
                )

        try:
            holder = model_id or route
            _log_event("gpu_lock_acquire_attempt", request_id=request_id, holder=holder)
            with lock.acquire(holder):
                _log_event("gpu_lock_acquired", request_id=request_id, holder=holder)
                await lifecycle.run_hooks(model.unload, ignore_errors=True)
                wait_for_vram_available(probe, model.required_vram_mb)
                response = await _proxy_request(model, route, request, body)
                if config.gpu.cooldown_seconds:
                    await asyncio.sleep(config.gpu.cooldown_seconds)
                _log_event(
                    "request_completed",
                    request_id=request_id,
                    route=route,
                    model_id=holder,
                    status_code=response.status_code,
                    uses_gpu=True,
                )
                return response
        except GPUBusyError as exc:
            _log_event("gpu_busy", request_id=request_id, route=route, model_id=model_id, holder=exc.holder)
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
            _log_event(
                "insufficient_vram",
                request_id=request_id,
                route=route,
                model_id=model_id,
                free_mb=exc.free_mb,
                required_mb=exc.required_mb,
            )
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
        except httpx.HTTPError as exc:
            _log_event(
                "request_upstream_error",
                request_id=request_id,
                route=route,
                model_id=model_id,
                error=str(exc),
            )
            return JSONResponse(
                status_code=502,
                content=error_payload(
                    "upstream_error",
                    "Upstream service returned an error",
                    True,
                ),
            )

    return app


def _extract_model_id(request: Request, body: bytes) -> str | None:
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type or not body:
        return None
    try:
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
    excluded = {"transfer-encoding", "connection", "content-encoding", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}
    fwd_headers = {k: v for k, v in upstream_response.headers.items() if k.lower() not in excluded}
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=fwd_headers,
        media_type=media_type,
    )
