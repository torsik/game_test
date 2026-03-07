import logging
import os
import secrets
import sqlite3
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from threading import Lock
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends, Header, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH       = os.getenv("DB_PATH",       "/data/codes.db")
ADMIN_KEY     = os.getenv("ADMIN_KEY",     "changeme")
ADMIN_UI_PASS = os.getenv("ADMIN_UI_PASS", "adminpass")
LOG_PATH      = os.getenv("LOG_PATH",      "/data/app.log")
UPLOAD_DIR    = Path(os.getenv("UPLOAD_DIR", "/data/uploads")).resolve()

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".zip", ".txt", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# Rate limits
RATE_LIMIT_ATTEMPTS       = int(os.getenv("RATE_LIMIT_ATTEMPTS", "10"))
RATE_LIMIT_WINDOW         = int(os.getenv("RATE_LIMIT_WINDOW",   "60"))
ADMIN_RATE_LIMIT_ATTEMPTS = 5
ADMIN_RATE_LIMIT_WINDOW   = 300  # 5 minutes

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("codelookup")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
for h in [logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding="utf-8")]:
    h.setFormatter(formatter)
    logger.addHandler(h)

# ── Rate Limiter ──────────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, max_attempts: int, window: int):
        self.max_attempts = max_attempts
        self.window = window
        self._store: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, ip: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            self._store[ip] = [t for t in self._store[ip] if now - t < self.window]
            if len(self._store[ip]) >= self.max_attempts:
                retry_after = int(self.window - (now - self._store[ip][0]))
                return False, retry_after
            self._store[ip].append(now)
            return True, 0

code_limiter  = RateLimiter(RATE_LIMIT_ATTEMPTS, RATE_LIMIT_WINDOW)
admin_limiter = RateLimiter(ADMIN_RATE_LIMIT_ATTEMPTS, ADMIN_RATE_LIMIT_WINDOW)

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT UNIQUE NOT NULL,
            message     TEXT,
            file_path   TEXT,
            file_name   TEXT,
            file_token  TEXT UNIQUE,  -- opaque token for file access, NOT guessable
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    # Migrate existing DB
    for col in ["file_path TEXT", "file_name TEXT", "file_token TEXT UNIQUE"]:
        try:
            conn.execute(f"ALTER TABLE codes ADD COLUMN {col}")
        except Exception:
            pass
    conn.commit()
    # Seed example data
    if conn.execute("SELECT COUNT(*) FROM codes").fetchone()[0] == 0:
        conn.executemany("INSERT INTO codes (code, message) VALUES (?, ?)", [
            ("ALPHA-001", "Congratulations! Your promo code: SAVE50"),
            ("BETA-2024", "Welcome to the beta program!"),
            ("VIP-GOLD",  "VIP Gold member. Meeting on Friday at 3pm."),
        ])
        conn.commit()
    conn.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Code Lookup", lifespan=lifespan, docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory="/app/templates")
# /uploads is NOT mounted as static — files only via /api/file/{token}

# ── Helpers ───────────────────────────────────────────────────────────────────
def get_ip(request: Request) -> str:
    return request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()

def require_admin(request: Request, x_admin_key: str = Header(...)):
    ip = get_ip(request)
    if not secrets.compare_digest(x_admin_key, ADMIN_KEY):
        logger.warning(f"ADMIN_FAIL   ip={ip}  path={request.url.path}")
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.info(f"ADMIN_OK     ip={ip}  path={request.url.path}")

def safe_file_path(file_path: str) -> Path:
    resolved = Path(file_path).resolve()
    if not str(resolved).startswith(str(UPLOAD_DIR)):
        raise HTTPException(status_code=403, detail="Forbidden")
    return resolved

# ── Schemas ───────────────────────────────────────────────────────────────────
class CheckRequest(BaseModel):
    code: str

class VerifyAdminUI(BaseModel):
    password: str

# ── Public routes ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/check")
async def check_code(body: CheckRequest, request: Request):
    ip = get_ip(request)
    allowed, retry_after = code_limiter.is_allowed(ip)
    if not allowed:
        logger.warning(f"RATE_LIMIT   ip={ip}  retry_after={retry_after}s")
        return JSONResponse(
            status_code=429,
            content={"success": False, "error": "rate_limit", "retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )

    code = body.code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Code is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT message, file_path, file_name, file_token FROM codes WHERE code = ?", (code,)
    ).fetchone()
    conn.close()

    if row:
        logger.info(f"CODE_HIT     ip={ip}  code={code}")
        return {
            "success":   True,
            "message":   row["message"],
            "has_file":  bool(row["file_path"]),
            "file_name": row["file_name"],
            # Return opaque token — NOT the user code
            "file_url":  f"/api/file/{row['file_token']}" if row["file_path"] else None,
        }

    logger.info(f"CODE_MISS    ip={ip}  code={code}")
    return {"success": False}


@app.get("/api/file/{token}")
async def get_file(token: str, request: Request):
    """
    File access by opaque token (uuid4, 32 hex chars).
    Token is unguessable — brute force is infeasible (10^38 combinations).
    """
    ip = get_ip(request)

    # Rate limit file downloads
    allowed, retry_after = code_limiter.is_allowed(f"file:{ip}")
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many requests")

    # Lookup by token — NOT by user code
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT file_path, file_name FROM codes WHERE file_token = ?", (token,)
    ).fetchone()
    conn.close()

    if not row or not row["file_path"]:
        raise HTTPException(status_code=404, detail="File not found")

    path = safe_file_path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    logger.info(f"FILE_DL      ip={ip}  token={token[:8]}...  file={row['file_name']}")
    return FileResponse(path, filename=row["file_name"])


# ── Admin UI auth ─────────────────────────────────────────────────────────────
@app.post("/api/admin/verify-ui")
async def verify_admin_ui(body: VerifyAdminUI, request: Request):
    ip = get_ip(request)
    allowed, retry_after = admin_limiter.is_allowed(ip)
    if not allowed:
        logger.warning(f"ADMIN_BRUTE  ip={ip}  blocked for {retry_after}s")
        return JSONResponse(
            status_code=429,
            content={"success": False, "error": "rate_limit", "retry_after": retry_after},
        )
    if secrets.compare_digest(body.password, ADMIN_UI_PASS):
        logger.info(f"ADMIN_LOGIN  ip={ip}")
        return {"success": True}

    logger.warning(f"ADMIN_UI_FAIL ip={ip}")
    return JSONResponse(status_code=401, content={"success": False})

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

# ── Admin API ─────────────────────────────────────────────────────────────────
@app.get("/api/admin/codes", dependencies=[Depends(require_admin)])
async def list_codes():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, code, message, file_name, created_at FROM codes ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/admin/codes", dependencies=[Depends(require_admin)])
async def add_code(
    request: Request,
    code:    str        = Form(...),
    message: str        = Form(""),
    file:    UploadFile = File(None),
):
    ip = get_ip(request)
    code = code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    if not message.strip() and (not file or not file.filename):
        raise HTTPException(status_code=400, detail="message or file is required")

    file_path  = None
    file_name  = None
    file_token = None

    if file and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File type not allowed: {ext}")
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large (max 10MB)")

        safe_name  = f"{uuid.uuid4().hex}{ext}"   # random name on disk
        file_token = uuid.uuid4().hex              # separate opaque access token
        file_path  = str(UPLOAD_DIR / safe_name)
        file_name  = file.filename

        with open(file_path, "wb") as f:
            f.write(content)

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO codes (code, message, file_path, file_name, file_token) VALUES (?, ?, ?, ?, ?)",
            (code, message.strip() or None, file_path, file_name, file_token),
        )
        conn.commit()
        conn.close()
        logger.info(f"CODE_ADDED   ip={ip}  code={code}  has_file={bool(file_path)}")
        return {"success": True}
    except sqlite3.IntegrityError:
        if file_path:
            Path(file_path).unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="Code already exists")

@app.delete("/api/admin/codes/{code_id}", dependencies=[Depends(require_admin)])
async def delete_code(code_id: int, request: Request):
    ip = get_ip(request)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT file_path FROM codes WHERE id = ?", (code_id,)).fetchone()
    if row and row["file_path"]:
        try:
            safe_file_path(row["file_path"]).unlink(missing_ok=True)
        except HTTPException:
            pass
    conn.execute("DELETE FROM codes WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()
    logger.info(f"CODE_DELETED ip={ip}  id={code_id}")
    return {"success": True}
