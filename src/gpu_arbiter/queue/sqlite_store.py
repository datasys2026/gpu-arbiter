from __future__ import annotations

import json
import time

import aiosqlite

from gpu_arbiter.queue.models import Task, TaskStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id       TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    route         TEXT NOT NULL,
    method        TEXT NOT NULL,
    headers       TEXT NOT NULL,
    body          BLOB NOT NULL,
    status        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    result_status INTEGER,
    result_body   BLOB,
    result_headers TEXT,
    error         TEXT,
    completed_at  REAL
);
CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
);
INSERT OR IGNORE INTO metadata VALUES ('last_tenant', NULL);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_status ON tasks(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_completed_at ON tasks(completed_at);
"""

_TERMINAL = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


def _row_to_task(row: aiosqlite.Row) -> Task:
    return Task(
        task_id=row["task_id"],
        tenant_id=row["tenant_id"],
        model_id=row["model_id"],
        route=row["route"],
        method=row["method"],
        headers=json.loads(row["headers"]),
        body=row["body"],
        status=TaskStatus(row["status"]),
        created_at=row["created_at"],
        result_status=row["result_status"],
        result_body=row["result_body"],
        result_headers=json.loads(row["result_headers"]) if row["result_headers"] else None,
        error=row["error"],
        completed_at=row["completed_at"],
    )


class SQLiteTaskStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def create(self, task: Task) -> None:
        await self._db.execute(
            """INSERT INTO tasks
               (task_id, tenant_id, model_id, route, method, headers, body, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id, task.tenant_id, task.model_id, task.route, task.method,
                json.dumps(task.headers), task.body, task.status.value, task.created_at,
            ),
        )
        await self._db.commit()

    async def get(self, task_id: str) -> Task | None:
        async with self._db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_task(row) if row else None

    async def claim_next(self) -> Task | None:
        async with self._db.execute("SELECT value FROM metadata WHERE key = 'last_tenant'") as cur:
            row = await cur.fetchone()
        last_tenant = row["value"] if row else None

        async with self._db.execute(
            "SELECT DISTINCT tenant_id FROM tasks WHERE status = 'pending' ORDER BY tenant_id"
        ) as cur:
            tenants = [r["tenant_id"] for r in await cur.fetchall()]

        if not tenants:
            return None

        # rotate: tenants after last_tenant come first
        if last_tenant in tenants:
            idx = tenants.index(last_tenant)
            tenants = tenants[idx + 1 :] + tenants[: idx + 1]

        for tenant in tenants:
            async with self._db.execute(
                "SELECT * FROM tasks WHERE tenant_id = ? AND status = 'pending' ORDER BY created_at LIMIT 1",
                (tenant,),
            ) as cur:
                row = await cur.fetchone()
            if row:
                task = _row_to_task(row)
                await self._db.execute(
                    "UPDATE tasks SET status = 'running' WHERE task_id = ?", (task.task_id,)
                )
                await self._db.execute(
                    "UPDATE metadata SET value = ? WHERE key = 'last_tenant'", (tenant,)
                )
                await self._db.commit()
                task.status = TaskStatus.RUNNING
                return task

        return None

    async def update(self, task_id: str, **fields: object) -> None:
        if not fields:
            return
        new_status = fields.get("status")
        if isinstance(new_status, TaskStatus) and new_status in _TERMINAL:
            if "completed_at" not in fields:
                fields = {**fields, "completed_at": time.time()}

        col_map = {
            "status": "status",
            "result_status": "result_status",
            "result_body": "result_body",
            "result_headers": "result_headers",
            "error": "error",
            "completed_at": "completed_at",
        }
        set_parts = []
        values = []
        for key, value in fields.items():
            if key not in col_map:
                continue
            set_parts.append(f"{col_map[key]} = ?")
            if key == "status" and isinstance(value, TaskStatus):
                values.append(value.value)
            elif key == "result_headers" and isinstance(value, dict):
                values.append(json.dumps(value))
            else:
                values.append(value)

        if not set_parts:
            return
        values.append(task_id)
        await self._db.execute(
            f"UPDATE tasks SET {', '.join(set_parts)} WHERE task_id = ?", values
        )
        await self._db.commit()

    async def queue_depth(self, tenant_id: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE tenant_id = ? AND status = 'pending'",
            (tenant_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["cnt"] if row else 0

    async def list_tasks(self, tenant_id: str, status: TaskStatus | None = None) -> list[Task]:
        if status is None:
            async with self._db.execute(
                "SELECT * FROM tasks WHERE tenant_id = ?", (tenant_id,)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self._db.execute(
                "SELECT * FROM tasks WHERE tenant_id = ? AND status = ?",
                (tenant_id, status.value),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]

    async def active_counts(self) -> dict:
        async with self._db.execute(
            "SELECT status, tenant_id FROM tasks WHERE status IN ('pending', 'running')"
        ) as cur:
            rows = await cur.fetchall()
        pending = sum(1 for r in rows if r["status"] == "pending")
        running = sum(1 for r in rows if r["status"] == "running")
        tenants = sorted({r["tenant_id"] for r in rows})
        return {"pending": pending, "running": running, "tenants": tenants}

    async def delete_expired(self, ttl_seconds: float) -> int:
        cutoff = time.time() - ttl_seconds
        async with self._db.execute(
            "DELETE FROM tasks WHERE completed_at IS NOT NULL AND completed_at <= ?", (cutoff,)
        ) as cur:
            deleted = cur.rowcount
        await self._db.commit()
        return deleted
