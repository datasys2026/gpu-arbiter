from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RetriableError(Exception):
    """Raised by execute_fn when the task should be re-queued rather than failed."""


@dataclass
class Task:
    task_id: str
    tenant_id: str
    model_id: str
    route: str
    method: str
    headers: dict
    body: bytes
    status: TaskStatus
    created_at: float
    result_status: int | None = None
    result_body: bytes | None = None
    result_headers: dict | None = None
    error: str | None = None
    completed_at: float | None = None
