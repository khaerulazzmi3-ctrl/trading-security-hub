from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_NAME = "Trading Security Hub"
DB_PATH = Path(os.getenv("DATABASE_PATH", "./data/guardian.db"))
ADMIN_BOOTSTRAP_TOKEN = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "")
SESSION_TTL = max(5, int(os.getenv("SESSION_TTL_MINUTES", "30"))) * 60

# Outbound-only Telegram reporting. No polling/webhook/command handlers are registered.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_REPORTS_ENABLED = os.getenv("TELEGRAM_REPORTS_ENABLED", "false").lower() == "true"


def telegram_report(message: str) -> bool:
    """Best-effort report; Telegram failures never change access/security decisions."""
    if not (TELEGRAM_REPORTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    import json
    import urllib.error
    import urllib.request

    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message[:3900],
                          "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and result.get("ok") is True
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        # Never log the token or Telegram response.
        return False


def report_event(event: str, detail: str):
    telegram_report(f"[AI Ops Universe] {event}\n{detail}"[:3900])

ROLES = {"blocked", "ordinary", "admin", "privileged"}
app = FastAPI(title=APP_NAME, version="0.1.0", description="Backend API for authenticated access governance, Guardian incident handling, audit logs, and paper-trading records.")
app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "https://khaerulazzmi3-ctrl.github.io,http://localhost:8000,http://127.0.0.1:8000").split(",") if origin.strip()], allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS access_requests(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, requested_role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL, decided_by INTEGER, decided_at INTEGER);
        CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY, severity TEXT NOT NULL, subject TEXT NOT NULL, reason TEXT NOT NULL, source TEXT, status TEXT NOT NULL, created_at INTEGER NOT NULL, resolved_by INTEGER, resolved_at INTEGER);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_orders(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL, note TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL);
        """)


@app.on_event("startup")
def startup():
    init_db()


def audit(actor: str, action: str, target: str, detail: str = ""):
    with db() as c:
        c.execute("INSERT INTO audit(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)", (actor, action, target, detail[:1000], int(time.time())))


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + derived.hex()


def password_ok(password: str, stored: str) -> bool:
    try:
        salt_hex, expected = stored.split("$", 1)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class RegisterInput(BaseModel):
    username: str = Field(min_length=3, max_length=48, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


class LoginInput(BaseModel):
    username: str
    password: str


class AccessRequestInput(BaseModel):
    requested_role: Literal["ordinary", "admin", "privileged"] = "ordinary"


class IncidentInput(BaseModel):
    severity: Literal["low", "medium", "high", "critical"]
    subject: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=5, max_length=1000)
    source: str | None = Field(default=None, max_length=200)


class DecisionInput(BaseModel):
    decision: Literal["approve", "deny"]


class PaperOrderInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9._-]+$")
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0, le=1_000_000)
    note: str = Field(default="Paper trading only", max_length=300)


def current_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as c:
        row = c.execute("""SELECT u.id,u.username,u.role,u.active,s.expires_at,s.revoked
                           FROM sessions s JOIN users u ON u.id=s.user_id
                           WHERE s.token_hash=?""", (token_hash,)).fetchone()
    if not row or row["revoked"] or not row["active"] or row["expires_at"] < int(time.time()):
        raise HTTPException(401, "Session invalid or expired")
    if row["role"] == "blocked":
        raise HTTPException(403, "Account blocked")
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def require_roles(*roles):
    def guard(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Insufficient role")
        return user
    return guard


@app.get("/health")
def health():
    return {"status": "ok", "service": APP_NAME, "mode": "backend", "live_execution": False}


@app.post("/auth/register", status_code=201)
def register(data: RegisterInput, request: Request):
    with db() as c:
        if c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(403, "Public registration disabled after initial account. Ask an admin to provision an account.")
        if not ADMIN_BOOTSTRAP_TOKEN:
            raise HTTPException(503, "Set ADMIN_BOOTSTRAP_TOKEN before creating the first admin")
        supplied = request.headers.get("x-bootstrap-token", "")
        if not hmac.compare_digest(supplied, ADMIN_BOOTSTRAP_TOKEN):
            raise HTTPException(403, "Valid bootstrap token required")
        cur = c.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)", (data.username, password_hash(data.password), "admin", int(time.time())))
        user_id = cur.lastrowid
    audit(data.username, "bootstrap_admin_created", str(user_id))
    return {"created": True, "username": data.username, "role": "admin", "message": "Remove/rotate bootstrap token after initial provisioning."}


@app.post("/auth/login")
def login(data: LoginInput, request: Request):
    with db() as c:
        row = c.execute("SELECT id,username,password_hash,role,active FROM users WHERE username=?", (data.username,)).fetchone()
        if not row or not password_ok(data.password, row["password_hash"]):
            audit(data.username, "login_denied", data.username, "Invalid credentials")
            raise HTTPException(401, "Invalid credentials")
        if not row["active"] or row["role"] == "blocked":
            audit(data.username, "login_denied", data.username, "Account disabled or blocked")
            raise HTTPException(403, "Account disabled or blocked")
        token = secrets.token_urlsafe(40)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (token_hash, row["id"], int(time.time()) + SESSION_TTL))
    audit(row["username"], "login_success", str(row["id"]), f"source={request.client.host if request.client else 'unknown'}")
    report_event("Login berhasil", f"akun={row['username']} role={row['role']}")
    return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_TTL, "role": row["role"]}


@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None), user=Depends(current_user)):
    token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as c:
        c.execute("UPDATE sessions SET revoked=1 WHERE token_hash=?", (token_hash,))
    audit(user["username"], "logout", str(user["id"]))
    return {"revoked": True}


@app.get("/me")
def me(user=Depends(current_user)):
    return user


@app.post("/access-requests", status_code=201)
def request_access(data: AccessRequestInput, user=Depends(current_user)):
    if data.requested_role == "ordinary" and user["role"] == "ordinary":
        raise HTTPException(409, "Already has ordinary access")
    with db() as c:
        cur = c.execute("INSERT INTO access_requests(user_id,requested_role,created_at) VALUES(?,?,?)", (user["id"], data.requested_role, int(time.time())))
        request_id = cur.lastrowid
    audit(user["username"], "access_requested", str(request_id), data.requested_role)
    report_event("Permintaan akses", f"akun={user['username']} role_diminta={data.requested_role} status=pending")
    return {"id": request_id, "status": "pending", "requested_role": data.requested_role}


@app.get("/access-requests")
def list_access_requests(user=Depends(require_roles("admin", "privileged"))):
    with db() as c:
        rows = c.execute("""SELECT r.id,u.username,r.requested_role,r.status,r.created_at
                            FROM access_requests r JOIN users u ON u.id=r.user_id
                            ORDER BY r.created_at DESC LIMIT 200""").fetchall()
    return [dict(r) for r in rows]


@app.post("/access-requests/{request_id}/decision")
def decide_access(request_id: int, data: DecisionInput, user=Depends(require_roles("admin", "privileged"))):
    with db() as c:
        req = c.execute("SELECT * FROM access_requests WHERE id=?", (request_id,)).fetchone()
        if not req or req["status"] != "pending":
            raise HTTPException(404, "Pending request not found")
        # Istimewa is the owner/boss tier. Admin may not grant Admin or Istimewa;
        # only an existing Istimewa account can approve either elevated role.
        if req["requested_role"] in ("admin", "privileged") and user["role"] != "privileged":
            raise HTTPException(403, "Only Istimewa governance can grant Admin or Istimewa access")
        target = c.execute("SELECT role FROM users WHERE id=?", (req["user_id"],)).fetchone()
        if target and target["role"] == "privileged" and user["role"] != "privileged":
            raise HTTPException(403, "Admin cannot change the Istimewa account")
        status = "approved" if data.decision == "approve" else "denied"
        if data.decision == "approve":
            c.execute("UPDATE users SET role=? WHERE id=?", (req["requested_role"], req["user_id"]))
            c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (req["user_id"],))
        c.execute("UPDATE access_requests SET status=?,decided_by=?,decided_at=? WHERE id=?", (status, user["id"], int(time.time()), request_id))
    audit(user["username"], "access_request_"+status, str(request_id), req["requested_role"])
    report_event("Keputusan permintaan akses", f"request_id={request_id} role={req['requested_role']} status={status} oleh={user['username']}")
    return {"id": request_id, "status": status}


@app.post("/guardian/incidents", status_code=201)
def create_incident(data: IncidentInput, user=Depends(require_roles("admin", "privileged"))):
    status = "blocked" if data.severity in ("high", "critical") else "investigating"
    with db() as c:
        cur = c.execute("INSERT INTO incidents(severity,subject,reason,source,status,created_at) VALUES(?,?,?,?,?,?)",
                        (data.severity, data.subject, data.reason, data.source, status, int(time.time())))
        incident_id = cur.lastrowid
        # If subject matches an account, immediately revoke sessions on high-confidence severity.
        if status == "blocked":
            account = c.execute("SELECT id,role FROM users WHERE username=?", (data.subject,)).fetchone()
            if account and account["role"] == "privileged" and user["role"] != "privileged":
                c.execute("DELETE FROM incidents WHERE id=?", (incident_id,))
                raise HTTPException(403, "Admin cannot block the Istimewa account")
            if account:
                c.execute("UPDATE users SET role='blocked' WHERE id=?", (account["id"],))
                c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (account["id"],))
    audit(user["username"], "incident_created", str(incident_id), f"{data.severity}:{status}:{data.reason}")
    report_event("Guardian incident", f"id={incident_id} severity={data.severity} status={status} subject={data.subject} source={data.source or 'unknown'} reason={data.reason}")
    return {"id": incident_id, "status": status, "session_revoked_if_account_matched": status == "blocked"}


@app.get("/guardian/incidents")
def incidents(user=Depends(require_roles("admin", "privileged"))):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM incidents ORDER BY created_at DESC LIMIT 200").fetchall()]


@app.post("/guardian/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int, user=Depends(require_roles("admin", "privileged"))):
    with db() as c:
        row = c.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row or row["status"] == "resolved":
            raise HTTPException(404, "Open incident not found")
        c.execute("UPDATE incidents SET status='resolved',resolved_by=?,resolved_at=? WHERE id=?", (user["id"], int(time.time()), incident_id))
    audit(user["username"], "incident_resolved", str(incident_id))
    report_event("Guardian incident selesai", f"id={incident_id} oleh={user['username']}")
    return {"id": incident_id, "status": "resolved", "note": "Resolution is audited; account role restoration is a separate privileged action."}


@app.get("/audit")
def audit_log(user=Depends(require_roles("admin", "privileged"))):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY created_at DESC LIMIT 500").fetchall()]


@app.post("/paper/orders", status_code=201)
def paper_order(data: PaperOrderInput, user=Depends(require_roles("ordinary", "admin", "privileged"))):
    with db() as c:
        cur = c.execute("INSERT INTO paper_orders(user_id,symbol,side,quantity,note,status,created_at) VALUES(?,?,?,?,?,?,?)",
                        (user["id"], data.symbol.upper(), data.side, data.quantity, data.note, "recorded_paper_only", int(time.time())))
        order_id = cur.lastrowid
    audit(user["username"], "paper_order_recorded", str(order_id), f"{data.symbol.upper()} {data.side} {data.quantity}")
    report_event("Paper order dicatat", f"akun={user['username']} symbol={data.symbol.upper()} side={data.side} quantity={data.quantity}; tidak ada order broker dikirim")
    return {"id": order_id, "status": "recorded_paper_only", "message": "No broker or exchange order was sent."}


@app.get("/paper/orders")
def paper_orders(user=Depends(current_user)):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT id,symbol,side,quantity,note,status,created_at FROM paper_orders WHERE user_id=? ORDER BY created_at DESC LIMIT 200", (user["id"],)).fetchall()]


# Agent Operations routes are isolated from the core access-governance handlers.
from app.agent_tasks import router as agent_tasks_router
app.include_router(agent_tasks_router)
