"""Bounded, auditable Agent Task Engine foundation.

This module deliberately does not call external AI APIs or execute trades. Tasks
run only when an authenticated user explicitly requests execution, and supported
task kinds are allow-listed until the Grok adapter is configured separately.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.main import DB_PATH, audit, current_user, require_roles

router = APIRouter(prefix="/agent/tasks", tags=["Agent Operations"])
TASK_KINDS = {"plan_only", "system_check"}
TASK_STATUSES = {"queued", "planning", "running", "waiting_approval", "completed", "failed", "cancelled"}


def task_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_agent_task_tables() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_tasks(
            id TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            agent_name TEXT NOT NULL,
            kind TEXT NOT NULL,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            started_at INTEGER,
            finished_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner_created
            ON agent_tasks(owner_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS agent_task_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(task_id) REFERENCES agent_tasks(id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_task_events_task
            ON agent_task_events(task_id, id);
        """)


init_agent_task_tables()


class TaskCreate(BaseModel):
    objective: str = Field(min_length=5, max_length=2000)
    kind: Literal["plan_only", "system_check"] = "plan_only"
    agent_name: str = Field(default="ops-agent", min_length=2, max_length=60)


class TaskRunInput(BaseModel):
    confirm_run: bool = True


def _event(conn: sqlite3.Connection, task_id: str, event_type: str, message: str) -> None:
    conn.execute(
        "INSERT INTO agent_task_events(task_id,event_type,message,created_at) VALUES(?,?,?,?)",
        (task_id, event_type, message[:1000], int(time.time())),
    )


def _task_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    if item.get("result_json"):
        try:
            item["result"] = json.loads(item.pop("result_json"))
        except json.JSONDecodeError:
            item["result"] = {"text": item.pop("result_json")}
    else:
        item.pop("result_json", None)
        item["result"] = None
    return item


@router.post("", status_code=201)
def create_task(data: TaskCreate, user=Depends(require_roles("ordinary", "admin", "privileged"))):
    now = int(time.time())
    task_id = uuid.uuid4().hex
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO agent_tasks(id,owner_id,agent_name,kind,objective,status,progress,created_at,updated_at)
               VALUES(?,?,?,?,?,'queued',0,?,?)""",
            (task_id, user["id"], data.agent_name, data.kind, data.objective.strip(), now, now),
        )
        _event(conn, task_id, "queued", "Task dibuat dan menunggu eksekusi manual.")
    audit(user["username"], "agent_task_created", task_id, f"kind={data.kind}; agent={data.agent_name}")
    return {"id": task_id, "status": "queued", "progress": 0, "message": "Task tersimpan; belum dijalankan."}


@router.get("")
def list_tasks(limit: int = 50, user=Depends(current_user)):
    limit = max(1, min(limit, 100))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT * FROM agent_tasks WHERE owner_id=? ORDER BY created_at DESC LIMIT ?""",
            (user["id"], limit),
        ).fetchall()
    return [_task_dict(row) for row in rows]


@router.get("/{task_id}")
def get_task(task_id: str, user=Depends(current_user)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agent_tasks WHERE id=? AND owner_id=?", (task_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Task tidak ditemukan")
        events = conn.execute(
            "SELECT event_type,message,created_at FROM agent_task_events WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
    result = _task_dict(row)
    result["events"] = [dict(event) for event in events]
    return result


@router.post("/{task_id}/run")
def run_task(task_id: str, data: TaskRunInput, user=Depends(require_roles("ordinary", "admin", "privileged"))):
    if not data.confirm_run:
        raise HTTPException(400, "confirm_run harus true untuk menjalankan task")
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agent_tasks WHERE id=? AND owner_id=?", (task_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Task tidak ditemukan")
        if row["status"] != "queued":
            raise HTTPException(409, f"Task tidak bisa dijalankan dari status {row['status']}")
        conn.execute(
            "UPDATE agent_tasks SET status='planning',progress=10,updated_at=?,started_at=? WHERE id=?",
            (now, now, task_id),
        )
        _event(conn, task_id, "planning", "Memvalidasi jenis task dan batas eksekusi.")
        kind, objective = row["kind"], row["objective"]
        if kind not in TASK_KINDS:
            conn.execute(
                "UPDATE agent_tasks SET status='failed',error=?,updated_at=?,finished_at=? WHERE id=?",
                ("Jenis task tidak diizinkan.", now, now, task_id),
            )
            _event(conn, task_id, "failed", "Task dihentikan: jenis tidak diizinkan.")
            raise HTTPException(400, "Jenis task tidak diizinkan")
        conn.execute("UPDATE agent_tasks SET status='running',progress=40,updated_at=? WHERE id=?", (now, task_id))
        _event(conn, task_id, "running", "Menjalankan handler lokal yang diizinkan; tidak memanggil API eksternal.")

        if kind == "plan_only":
            result = {
                "mode": "local_plan_only",
                "ai_provider": "not_configured",
                "objective": objective,
                "steps": [
                    "Tentukan data dan sumber yang diperlukan.",
                    "Validasi data, waktu pembaruan, dan kelengkapan sumber.",
                    "Jalankan analisis sesuai aturan yang disetujui.",
                    "Catat bukti, asumsi, dan keterbatasan.",
                    "Minta persetujuan sebelum tindakan eksternal apa pun.",
                ],
                "limitations": [
                    "Grok belum dipanggil dari Agent Task Engine.",
                    "Tidak ada data pasar live yang diambil.",
                    "Tidak ada transaksi atau perubahan sistem yang dijalankan.",
                ],
            }
        else:
            result = {
                "mode": "local_system_check",
                "database_path_configured": bool(str(DB_PATH)),
                "task_kinds_allowlisted": sorted(TASK_KINDS),
                "external_ai_calls_enabled": False,
                "live_trading_enabled": False,
            }

        encoded = json.dumps(result, ensure_ascii=False)
        finished = int(time.time())
        conn.execute(
            """UPDATE agent_tasks SET status='completed',progress=100,result_json=?,error=NULL,
               updated_at=?,finished_at=? WHERE id=?""",
            (encoded, finished, finished, task_id),
        )
        _event(conn, task_id, "completed", "Task selesai dengan handler lokal; hasil tersimpan.")
    audit(user["username"], "agent_task_completed", task_id, f"kind={kind}")
    return {"id": task_id, "status": "completed", "progress": 100, "result": result}


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str, user=Depends(require_roles("ordinary", "admin", "privileged"))):
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status FROM agent_tasks WHERE id=? AND owner_id=?", (task_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Task tidak ditemukan")
        if row["status"] != "queued":
            raise HTTPException(409, "Hanya task queued yang dapat dibatalkan pada versi ini")
        conn.execute("UPDATE agent_tasks SET status='cancelled',updated_at=?,finished_at=? WHERE id=?", (now, now, task_id))
        _event(conn, task_id, "cancelled", "Task dibatalkan sebelum eksekusi.")
    audit(user["username"], "agent_task_cancelled", task_id)
    return {"id": task_id, "status": "cancelled"}


@router.get("/{task_id}/events")
def get_task_events(task_id: str, user=Depends(current_user)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        task = conn.execute("SELECT id FROM agent_tasks WHERE id=? AND owner_id=?", (task_id, user["id"])).fetchone()
        if not task:
            raise HTTPException(404, "Task tidak ditemukan")
        rows = conn.execute(
            "SELECT event_type,message,created_at FROM agent_task_events WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
    return [dict(row) for row in rows]
