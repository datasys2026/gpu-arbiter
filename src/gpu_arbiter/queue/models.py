from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


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
