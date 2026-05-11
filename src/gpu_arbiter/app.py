from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from gpu_arbiter.config import ArbiterConfig, ModelConfig
from gpu_arbiter.errors import error_payload
from gpu_arbiter.lifecycle import LifecycleRunner, UpstreamNotReadyError
from gpu_arbiter.locking import GPUBusyError, InMemoryGPULock
from gpu_arbiter.queue.models import RetriableError, Task, TaskStatus
from gpu_arbiter.queue.store import InMemoryTaskStore, TaskStore
from gpu_arbiter.queue.worker import QueueWorker
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
    task_execute_fn: Callable[[Task], Awaitable[tuple[int, bytes, dict]]] | None = None,
    worker_poll_interval: float = 1.0,
    task_ttl_seconds: float = 3600.0,
    cleanup_interval_seconds: float = 300.0,
) -> FastAPI:
    lock = gpu_lock or InMemoryGPULock()
    probe = vram_probe or StaticVRAMProbe(free_mb=0)
    lifecycle = lifecycle_runner or LifecycleRunner(logger=LOGGER)
    store: TaskStore = task_store or InMemoryTaskStore()
    execute_fn = task_execute_fn or _make_execute_fn(config, lock, probe, lifecycle)
    worker = QueueWorker(store=store, execute_fn=execute_fn, poll_interval=worker_poll_interval)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        import anyio
        await store.initialize()
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(worker.run)
                tg.start_soon(_cleanup_loop, store, task_ttl_seconds, cleanup_interval_seconds)
                yield
                tg.cancel_scope.cancel()
        finally:
            await store.close()

    app = FastAPI(title="GPU Arbiter", lifespan=lifespan)

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

    @app.get("/tasks")
    async def list_tasks(request: Request, status: str | None = None) -> Response:
        tenant_id = request.headers.get("x-tenant-id", "").strip()
        if not tenant_id:
            return JSONResponse(
                status_code=400,
                content=error_payload("missing_tenant", "X-Tenant-ID header is required", False),
            )
        filter_status = None
        if status:
            try:
                filter_status = TaskStatus(status)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content=error_payload("invalid_status", f"Unknown status: {status!r}", False),
                )
        tasks = await store.list_tasks(tenant_id, status=filter_status)
        return JSONResponse({
            "tasks": [
                {"task_id": t.task_id, "status": t.status.value, "model_id": t.model_id, "created_at": t.created_at}
                for t in tasks
            ]
        })

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

    @app.delete("/tasks/{task_id}")
    async def cancel_task(task_id: str, request: Request) -> Response:
        tenant_id = request.headers.get("x-tenant-id", "").strip()
        task = await store.get(task_id)
        if task is None or task.tenant_id != tenant_id:
            return JSONResponse(status_code=404, content={"error": "task not found"})
        if task.status != TaskStatus.PENDING:
            return JSONResponse(
                status_code=409,
                content=error_payload(
                    "task_not_cancellable",
                    f"Only pending tasks can be cancelled (current status: {task.status.value})",
                    False,
                ),
            )
        await store.update(task_id, status=TaskStatus.CANCELLED)
        return JSONResponse({"task_id": task_id, "status": "cancelled"})

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
                await wait_for_vram_available(probe, model.required_vram_mb)
                await lifecycle.wait_until_ready(model.health)
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
        except UpstreamNotReadyError as exc:
            _log_event(
                "upstream_not_ready",
                request_id=request_id,
                route=route,
                model_id=model_id,
                url=exc.url,
                timeout=exc.timeout,
            )
            return JSONResponse(
                status_code=503,
                content=error_payload(
                    "upstream_not_ready",
                    f"Upstream service did not become ready within {exc.timeout}s",
                    True,
                    url=exc.url,
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


_EXCLUDED_RESPONSE_HEADERS = frozenset({
    "transfer-encoding", "connection", "content-encoding", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade",
})
_EXCLUDED_REQUEST_HEADERS = frozenset({"host", "content-length"})


async def _proxy_request_raw(
    model: ModelConfig,
    route: str,
    method: str,
    headers: dict,
    body: bytes,
    params: object = None,
) -> tuple[int, bytes, dict]:
    upstream_url = model.upstream.rstrip("/") + route
    filtered = {k: v for k, v in headers.items() if k.lower() not in _EXCLUDED_REQUEST_HEADERS}
    kwargs: dict = {"content": body, "headers": filtered}
    if params:
        kwargs["params"] = params
    async with httpx.AsyncClient(timeout=900) as client:
        resp = await client.request(method, upstream_url, **kwargs)
    fwd_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _EXCLUDED_RESPONSE_HEADERS}
    return resp.status_code, resp.content, fwd_headers


async def _proxy_request(model: ModelConfig, route: str, request: Request, body: bytes) -> Response:
    status_code, content, fwd_headers = await _proxy_request_raw(
        model, route, request.method, dict(request.headers), body, params=request.query_params
    )
    media_type = fwd_headers.get("content-type")
    return Response(content=content, status_code=status_code, headers=fwd_headers, media_type=media_type)


def _make_execute_fn(
    config: ArbiterConfig,
    lock: InMemoryGPULock,
    probe: StaticVRAMProbe,
    lifecycle: LifecycleRunner,
) -> Callable[[Task], Awaitable[tuple[int, bytes, dict]]]:
    async def execute(task: Task) -> tuple[int, bytes, dict]:
        model = config.models.get(task.model_id)
        if model is None:
            raise ValueError(f"model {task.model_id!r} not configured")
        if not model.uses_gpu:
            return await _proxy_request_raw(model, task.route, task.method, task.headers, task.body)
        try:
            with lock.acquire(task.model_id):
                await lifecycle.run_hooks(model.unload, ignore_errors=True)
                await wait_for_vram_available(probe, model.required_vram_mb)
                await lifecycle.wait_until_ready(model.health)
                return await _proxy_request_raw(model, task.route, task.method, task.headers, task.body)
        except GPUBusyError as exc:
            raise RetriableError(f"GPU busy, held by {exc.holder}") from exc

    return execute


async def _cleanup_loop(store: TaskStore, ttl_seconds: float, interval_seconds: float) -> None:
    import anyio
    while True:
        await anyio.sleep(interval_seconds)
        await store.delete_expired(ttl_seconds)
