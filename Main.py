import os
import sqlite3
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from threading import Lock

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "/data/codes.db")
ADMIN_KEY = os.getenv("ADMIN_KEY", "changeme")

# Rate limit: max attempts per IP per window
RATE_LIMIT_ATTEMPTS = int(os.getenv("RATE_LIMIT_ATTEMPTS", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds


# ── Rate Limiter ──────────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self):
        self._store: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, ip: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            timestamps = self._store[ip]
            # Remove old entries
            self._store[ip] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
            if len(self._store[ip]) >= RATE_LIMIT_ATTEMPTS:
                retry_after = int(RATE_LIMIT_WINDOW - (now - self._store[ip][0]))
                return False, retry_after
            self._store[ip].append(now)
            return True, 0


rate_limiter = RateLimiter()


# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            code      TEXT UNIQUE NOT NULL,
            message   TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    # Seed example data if empty
    if conn.execute("SELECT COUNT(*) FROM codes").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO codes (code, message) VALUES (?, ?)",
            [
                ("ALPHA-001", "Поздравляем! Ваш промокод: SAVE50"),
                ("BETA-2024", "Добро пожаловать в бета-программу! Ссылка: https://internal.example.com/beta"),
                ("VIP-GOLD",  "Вы VIP Gold участник. Встреча в пятницу в 15:00."),
            ],
        )
        conn.commit()
    conn.close()


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Code Lookup", lifespan=lifespan, docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory="/app/templates")


# ── Auth helper ───────────────────────────────────────────────────────────────
def require_admin(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Schemas ───────────────────────────────────────────────────────────────────
class CheckRequest(BaseModel):
    code: str

class CodeCreate(BaseModel):
    code: str
    message: str


# ── Public routes ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/check")
async def check_code(body: CheckRequest, request: Request):
    ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()

    allowed, retry_after = rate_limiter.is_allowed(ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"success": False, "message": f"Слишком много попыток. Подождите {retry_after} сек."},
            headers={"Retry-After": str(retry_after)},
        )

    code = body.code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT message FROM codes WHERE code = ?", (code,)).fetchone()
    conn.close()

    if row:
        return {"success": True, "message": row["message"]}
    return {"success": False, "message": None}


# ── Admin routes ──────────────────────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/api/admin/codes", dependencies=[Depends(require_admin)])
async def list_codes():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, code, message, created_at FROM codes ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/admin/codes", dependencies=[Depends(require_admin)])
async def add_code(body: CodeCreate):
    code = body.code.strip().upper()
    if not code or not body.message.strip():
        raise HTTPException(status_code=400, detail="code and message are required")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO codes (code, message) VALUES (?, ?)", (code, body.message.strip()))
        conn.commit()
        conn.close()
        return {"success": True}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Code already exists")


@app.delete("/api/admin/codes/{code_id}", dependencies=[Depends(require_admin)])
async def delete_code(code_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM codes WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()
    return {"success": True}
