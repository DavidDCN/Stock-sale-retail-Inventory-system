"""
server.py  =  the API that runs on the LAPTOP. The phone app (Flutter) AND the React desktop app talk to this.
Lives in  Tress/backend/  together with ocr_engine.py, shop_api.py, shop_db.py and the database inventory.db.

  cd backend
  python server.py serve                                                       (start the server)
  python server.py set-system-password                                         (forgot the system password)
  python server.py add-user alice --name "Alice Reyes" --avatar alice.jpg     (a phone account, asks for a password)

Desktop sign-in: the first time you choose the SYSTEM PASSWORD. After that, anyone who knows it can press
"New person" and make their own username + password (and is then shown as "in charge" in the Calendar page).
Sales people are normally created from React: Cash Flow -> Employee -> "Phone login".

It reuses ocr_engine.py (the same OCR + matching + stock logic as the desktop window):
the phone uploads a photo -> it waits in a queue -> the OCR process reads it -> the rows are stored ->
the phone shows them for checking -> "Update" deducts the stock and records the sale.

Open  http://<laptop-ip>:8000/docs  in a browser for the interactive API documentation.
"""

import argparse
import getpass
import os
import hashlib
import hmac
import io
import json
import queue
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PIL import Image, ImageOps
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:          # works no matter which folder you start it from
    sys.path.insert(0, str(BASE))

import ocr_engine as E
import shop_api
import shop_db
DATA = E.APP_DIR                    # backend/ with Python; AppData\Local\TresNinosShop for the .exe
UPLOAD_DIR = DATA / "uploads"
THUMB_DIR = UPLOAD_DIR / "thumbs"
AVATAR_DIR = DATA / "avatars"
# the built React app (npm run build -> dist/). The .exe carries it inside as "web".
WEB_DIR = next((d for d in (Path(getattr(sys, "_MEIPASS", BASE)) / "web", BASE.parent / "dist") if (d / "index.html").exists()), None)
SESSION_DAYS = 30
MAX_UPLOAD_MB = 15
APP_ID = "tres-ninos-shop"          # phones recognise this server by this name (in /api/ping and the Wi-Fi answer)
# Changes with every update of these files. The desktop page has the same number (src/lib/api.js SERVER_VERSION): when they
# differ, the page shows "restart python server.py serve" (an old server still running does not know the new pages).
SERVER_VERSION = "2026.11.04-r23"
DISCOVERY_PORT = 8765              # UDP: a phone shouts "TRESNINOS_DISCOVER" on the Wi-Fi, this server answers
SERVE_PORT = 8000                  # set by "serve --port"
MAX_LOGIN_FAILS = 5                # wrong passwords allowed ...
LOGIN_WINDOW_SEC = 300             # ... per this many seconds, per device (address)
# Desktop pages (owner data) answer only on the laptop itself. Set SHOP_ALLOW_REMOTE_DESK=1 (or serve
# --allow-remote-desk) to also allow other PCs on the Wi-Fi.
ALLOW_REMOTE_DESK = os.environ.get("SHOP_ALLOW_REMOTE_DESK") == "1"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# =====================================================================
# DATABASE (same inventory.db file the desktop window uses)
# =====================================================================
def db():
    # check_same_thread=False: FastAPI opens the connection (get_db) on one worker thread and may run the endpoint
    # on another. Each request still has its OWN connection used by one thing at a time, so this is safe.
    # Without it, requests failed at random with "SQLite objects created in a thread can only be used in that same thread".
    conn = sqlite3.connect(E.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_db():                       # FastAPI dependency: one connection per request, always closed
    conn = db()
    try:
        yield conn
    finally:
        conn.close()


def init_server_db():
    conn = E.init_db()              # items, sales, sale_items, ocr_runs (+ migrations)
    conn.execute("PRAGMA journal_mode=WAL")      # lets the desktop window and the server share the file
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            display_name TEXT, password_hash TEXT NOT NULL,
            avatar TEXT, active INTEGER NOT NULL DEFAULT 1, created TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            created TEXT, expires TEXT NOT NULL, last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS receipts (                -- one row per photo sent by a phone (this is also the log)
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, client_id TEXT, image TEXT NOT NULL,
            status TEXT NOT NULL,                            -- queued | processing | done | failed
            error TEXT, created TEXT, started TEXT, finished TEXT,
            seconds REAL, device TEXT, model TEXT,
            UNIQUE(user_id, client_id)
        );
        CREATE TABLE IF NOT EXISTS receipt_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL, ocr_text TEXT, item_id INTEGER NOT NULL,
            score REAL, qty REAL, verified INTEGER NOT NULL DEFAULT 0,
            sold INTEGER NOT NULL DEFAULT 0, sale_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS receipt_unmatched (           -- order lines on the photo that the shop does NOT carry
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL, ocr_text TEXT, item_text TEXT, qty REAL
        );
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
    if "kind" not in cols:               # 'desk' = signed in on the desktop WITH the system password, 'phone' = the phone app
        conn.execute("ALTER TABLE sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'phone'")
    try:                                 # desktop accounts: the username IS the name everywhere (Calendar, logs)
        conn.execute("UPDATE users SET display_name=username WHERE is_admin=1 AND IFNULL(display_name,'')<>username")
    except sqlite3.OperationalError:     # very old database without is_admin yet (added by shop_db on first start)
        pass
    conn.commit()
    conn.close()


# =====================================================================
# USERS + LOGIN
# =====================================================================
def hash_password(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def check_password(pw, stored):
    try:
        _, salt, dk = stored.split("$")
        new = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000)
        return hmac.compare_digest(new.hex(), dk)
    except Exception:
        return False


def save_avatar(src_path, username):
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    im = ImageOps.exif_transpose(Image.open(src_path)).convert("RGB")
    im = ImageOps.fit(im, (256, 256))                     # square, centred
    name = f"{username.lower()}.jpg"
    im.save(AVATAR_DIR / name, "JPEG", quality=88)
    return name


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(authorization: Optional[str] = Header(None), conn=Depends(get_db)):
    """Every protected endpoint needs   Authorization: Bearer <token>   (the token comes from /api/login)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not signed in")
    th = token_hash(authorization[7:].strip())
    row = conn.execute("""SELECT u.*, s.expires, s.kind AS session_kind FROM sessions s JOIN users u ON u.id = s.user_id
                          WHERE s.token_hash = ?""", (th,)).fetchone()
    if not row or row["expires"] < now_iso() or not row["active"]:
        raise HTTPException(401, "Session expired or account disabled. Please sign in again.")
    t = now_iso()
    with conn:
        conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (t, th))
        # who used the system on which day, first and last time = "in charge" on the Calendar page
        conn.execute("""INSERT INTO user_activity(user_id, date, first_seen, last_seen) VALUES(?,?,?,?)
                        ON CONFLICT(user_id, date) DO UPDATE SET last_seen=excluded.last_seen""", (row["id"], t[:10], t, t))
    return row


FAILS = {}                           # client address -> [timestamps of wrong passwords] (any sign-in form)


def client_ip(request):
    return request.client.host if request.client else "?"


def too_many_fails(request):
    key, t = client_ip(request), time.time()
    FAILS[key] = [x for x in FAILS.get(key, []) if t - x < LOGIN_WINDOW_SEC]
    if len(FAILS[key]) >= MAX_LOGIN_FAILS:
        wait = int(LOGIN_WINDOW_SEC - (t - FAILS[key][0])) // 60 + 1
        raise HTTPException(429, f"Too many wrong passwords from this device. Wait {wait} minute(s) and try again.")


def note_fail(request):
    FAILS.setdefault(client_ip(request), []).append(time.time())


_OWN = {"t": 0.0, "ips": set()}


def own_addresses():
    """This laptop's own addresses (refreshed every minute: the Wi-Fi address can change)."""
    if time.time() - _OWN["t"] > 60:
        _OWN["ips"] = set(lan_addresses())
        _OWN["t"] = time.time()
    return _OWN["ips"]


def is_local(request):
    """True when the request comes from the shop laptop itself (the desktop app), not from a phone or another PC."""
    host = client_ip(request)
    return host in ("::1", "localhost") or host.startswith("127.") or host in own_addresses()


def require_local(request):
    if not (ALLOW_REMOTE_DESK or is_local(request)):
        raise HTTPException(403, "The desktop pages only open on the shop laptop itself. Phones use the shop app.")


def require_desk(request, user):
    """Owner data: a desktop account, signed in WITH the system password, on the shop laptop."""
    if not user["is_admin"]:
        raise HTTPException(403, "Desktop account required (this is a phone login)")
    if user["session_kind"] != "desk":
        raise HTTPException(401, "Please sign in on the desktop with the system password.")
    require_local(request)


# =====================================================================
# OCR SERVICE: owns the OCR process, a FIFO queue, and stores the results
# =====================================================================
class OcrService:
    def __init__(self, device="auto", tier="medium", side=E.DEFAULT_SIDE):
        self.cfg_device, self.tier, self.side = device, tier, side
        self.device, self.tier_used = None, tier
        self.worker = None
        self.ready = False
        self.broken = ""                         # non-empty = the OCR engine cannot start
        self.pending = deque()                   # (receipt_id, image_path) waiting for the model
        self.inflight = {}                       # receipt_id -> path, handed to the OCR process
        self.crashes = 0
        self.started_at = time.time()
        self.force_cpu = False
        self.fast = False                        # the model runs in the fast CPU mode (ocr_engine.fast_mode_wanted)
        self.safe = False                        # after a crash: load without the fast CPU mode
        self.waiters = {}                        # "tmp-..." tag -> {"event", "result", "error"}: one-off reads (Add Order photo)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None

    def configure(self, device, tier, side):
        self.cfg_device, self.tier, self.side = device, tier, side
        self.tier_used = tier

    # ---- lifecycle
    def start(self):
        self.stopping.clear()
        self._spawn()
        self.thread = threading.Thread(target=self._pump, daemon=True, name="ocr-pump")
        self.thread.start()
        conn = db()                              # after a restart / power loss: pick up unfinished receipts
        rows = conn.execute("SELECT id, image FROM receipts WHERE status IN ('queued','processing') ORDER BY id").fetchall()
        conn.close()
        for r in rows:
            self.submit(r["id"], r["image"])

    def stop(self):
        self.stopping.set()
        if self.worker:
            self.worker.stop()

    def _spawn(self):
        self.ready = False
        self.worker = E.OcrWorker()
        self.worker.start()

    def status(self):
        return {"ready": self.ready, "device": self.device, "model": self.tier_used, "fastMode": self.fast,
                "queued": len(self.pending) + len(self.inflight), "error": self.broken or None,
                "startingFor": None if self.ready else int(time.time() - self.started_at),
                "bundledModels": bool(E.bundled_models())}

    # ---- queue
    def submit(self, rid, path):
        with self.lock:
            self.pending.append((rid, path))
            self._flush()

    def read_now(self, path, timeout=180):
        """Read ONE image and wait for the text boxes (used by Sales -> Add Order -> Read from photo).
        Uses the same queue and model as the phone receipts. Returns the OCR result dict, raises RuntimeError."""
        if self.broken:
            raise RuntimeError(self.broken)
        tag = f"tmp-{secrets.token_hex(6)}"
        w = {"event": threading.Event(), "result": None, "error": None}
        with self.lock:
            self.waiters[tag] = w
        self.submit(tag, str(path))
        try:
            if not w["event"].wait(timeout):
                raise RuntimeError("The receipt reader is busy or still loading. Try again in a minute.")
            if w["error"]:
                raise RuntimeError(w["error"])
            return w["result"]
        finally:
            with self.lock:
                self.waiters.pop(tag, None)
                self.pending = deque(x for x in self.pending if x[0] != tag)

    def _finish_temp(self, tag, result=None, error=None):
        w = self.waiters.get(tag)
        if w:
            w["result"], w["error"] = result, error
            w["event"].set()

    def forget(self, rid):
        """A receipt was deleted: take it out of the waiting line. (If the OCR process is already reading it, it
        cannot be cancelled; _store_result notices the receipt is gone and throws the result away.)"""
        with self.lock:
            self.pending = deque(x for x in self.pending if x[0] != rid)

    def _flush(self):                            # caller holds the lock
        if not self.ready:
            return
        while self.pending:
            rid, path = self.pending.popleft()
            self.inflight[rid] = path
            if not str(rid).startswith("tmp-"):
                self._set_status(rid, "processing", started=now_iso())
            self.worker.jobs.put(("ocr", path, self.side, self.device, self.tier_used, rid))

    # ---- messages from the OCR process
    def _pump(self):
        while not self.stopping.is_set():
            try:
                kind, data = self.worker.out.get(timeout=0.5)
            except queue.Empty:
                self._check_alive()
                continue
            try:
                self._handle(kind, data)
            except Exception:
                E.log("[server] error while handling OCR message:\n" + traceback.format_exc())

    def _handle(self, kind, d):
        if kind == "gpu_info":
            if self.cfg_device in ("cpu", "gpu"):
                dev = self.cfg_device
            else:
                dev = "gpu" if d["ok"] and not self.force_cpu else "cpu"
            if self.force_cpu:
                dev = "cpu"
            E.log(f"[server] GPU usable: {d['ok']} {d.get('name', '')} -> loading the model on {dev.upper()}")
            self.device = dev
            self.worker.jobs.put(("load", dev, self.tier, self.safe))
        elif kind == "model_ready":
            with self.lock:
                self.device, self.tier_used = d["device"], d["tier"]
                self.fast = bool(d.get("fast"))
                self.ready = True
                E.log(f"[server] OCR ready ({d['device'].upper()} / {d['tier']}). {d.get('note', '')}")
                self._flush()
        elif kind == "load_error":
            E.log("[server] model failed to load: " + E.short_error(d["msg"]))
            if d["device"] == "gpu":
                self.device = "cpu"
                self.worker.jobs.put(("load", "cpu", d["tier"], self.safe))
            else:
                self.broken = E.short_error(d["msg"])
                self._fail_all(self.broken)
        elif kind == "ocr_done":
            if str(d.get("tag")).startswith("tmp-"):
                with self.lock:
                    self.inflight.pop(d["tag"], None)
                self._finish_temp(d["tag"], result=d)
            else:
                if d.get("turned"):              # the photo was sideways / upside down and is stored upright now:
                    for t in THUMB_DIR.glob(f"{d['tag']}_*.jpg"):     # its small copies are made again
                        t.unlink(missing_ok=True)
                self._store_result(d)
        elif kind == "job_error":
            with self.lock:
                self.inflight.pop(d["tag"], None)
            if str(d.get("tag")).startswith("tmp-"):
                self._finish_temp(d["tag"], error=E.short_error(d["msg"]))
                return
            self._set_status(d["tag"], "failed", error=E.short_error(d["msg"]), finished=now_iso())
            E.log(f"[server] receipt {d['tag']} failed: {E.short_error(d['msg'])}")

    def _check_alive(self):
        if self.broken:                          # already gave up: do not log a crash every 0.5 s
            return
        if self.stopping.is_set() or self.worker.is_alive():
            return
        self.crashes += 1
        with self.lock:
            busy = bool(self.inflight)
        E.log(f"[server] the OCR process stopped unexpectedly (crash #{self.crashes}{', while reading' if busy else ''})")
        if self.fast and busy:                   # it died READING in the fast CPU mode: never use that mode here again
            E.fast_mode_failed(E.FALSE_OFF_REASON + (" (second time)" if E.FAST_RETRIED_FILE.exists() else ""))
        self.fast, self.safe = False, True
        with self.lock:
            lost = list(self.inflight.items())
            self.inflight.clear()
            if self.crashes > 3:
                self.broken = "The OCR engine keeps crashing. Run: python ocr_engine.py --info"
                self._fail_all(self.broken)
                return
            self.force_cpu = True
            for rid, path in reversed(lost):         # unfinished work goes back to the FRONT of the queue
                if not str(rid).startswith("tmp-"):
                    self._set_status(rid, "queued")
                self.pending.appendleft((rid, path))
            self._spawn()

    def _fail_all(self, msg):
        with self.lock:
            ids = [r for r, _ in self.pending] + list(self.inflight)
            self.pending.clear()
            self.inflight.clear()
        for rid in ids:
            if str(rid).startswith("tmp-"):
                self._finish_temp(rid, error=msg)
            else:
                self._set_status(rid, "failed", error=msg, finished=now_iso())

    # ---- database writes (own connection: this runs in the pump thread)
    def _set_status(self, rid, status, error=None, started=None, finished=None):
        conn = db()
        with conn:
            conn.execute("""UPDATE receipts SET status=?, error=?, started=COALESCE(?, started),
                            finished=COALESCE(?, finished) WHERE id=?""", (status, error, started, finished, rid))
        conn.close()

    def _store_result(self, d):
        rid = d["tag"]
        conn = db()
        try:
            if conn.execute("SELECT 1 FROM receipts WHERE id=?", (rid,)).fetchone() is None:
                E.log(f"[server] receipt {rid} was deleted while it was being read: result dropped")
                return
            lines, unmatched = E.build_lines_full(d["boxes"], conn)   # row grouping + matching to the database
            ignored = len(unmatched)
            owner, image = conn.execute("SELECT user_id, image FROM receipts WHERE id=?", (rid,)).fetchone()
            try:
                texts = E.group_rows(d["boxes"])
                items_by_id = {i["id"]: i for i in E.get_items(conn)}
            except Exception:
                texts, items_by_id = [], {}
            # a delivery receipt is mostly handwritten: its article table is matched more loosely for the owner's
            # ready-filled order (every row marked "Please check"); the phone's own rows stay as they are
            d_lines, d_unmatched = lines, unmatched
            if texts and shop_api.read_delivery(texts):
                try:
                    d_lines, d_unmatched = E.build_lines_full(d["boxes"], conn, loose=True)
                except Exception:
                    pass
            # Cash or bank is NOT read from the photo any more (round 17): the sales person picks it on the phone when
            # pressing Update (commit). A photo read again keeps the choice already made.
            with conn:
                conn.execute("DELETE FROM receipt_lines WHERE receipt_id=?", (rid,))
                # rows the sales person already sent to the owner as new items stay (they wait under Supply)
                conn.execute("DELETE FROM receipt_unmatched WHERE receipt_id=? AND req_at IS NULL", (rid,))
                for l in lines:                                       # price = what the receipt says, if it says
                    conn.execute("INSERT INTO receipt_lines(receipt_id, ocr_text, item_id, score, qty, verified, price, item_text) "
                                 "VALUES(?,?,?,?,?,0,?,?)", (rid, l["ocr"], l["item_id"], l["score"], l["qty"],
                                                             l.get("price"), l.get("item")))
                sent = {x[0] for x in conn.execute("SELECT ocr_text FROM receipt_unmatched WHERE receipt_id=? AND req_at IS NOT NULL",
                                                   (rid,))}
                for u in unmatched:                                   # shown on the phone as "not sold in this shop"
                    if u["ocr"] in sent:                              # already sent to the owner as a new item
                        continue
                    conn.execute("INSERT INTO receipt_unmatched(receipt_id, ocr_text, item_text, qty, price, unit) "
                                 "VALUES(?,?,?,?,?,?)", (rid, u["ocr"], u["item"], u["qty"], u.get("price"), u.get("unit")))
                conn.execute("""UPDATE receipts SET status='done', error=NULL, finished=?, seconds=?, device=?, model=?
                                WHERE id=?""", (now_iso(), d["seconds"], d["device"], d["tier"], rid))
                # a DELIVERY receipt ("Delivery receipt", "Deliver to", "D.R. No.") also becomes a ready-filled order for
                # the owner: green bubble on that person's Sales counter + top card on the dashboard
                draft = shop_api.save_delivery_draft(conn, rid, owner, texts, d_lines, d_unmatched, items_by_id)
            E.log_run(conn, d["device"], d["tier"], int(d["side"]), d["w"], d["h"], d["seconds"], len(d["boxes"]))
            E.log(f"[server] receipt {rid} done in {d['seconds']:.1f} s: {len(lines)} item rows, {ignored} not sold here"
                  + (f" · delivery order #{draft} for the owner" if draft else ""))
        finally:
            conn.close()
            with self.lock:
                self.inflight.pop(rid, None)


SERVICE = OcrService()


def start_discovery():
    """Answers phones on the same Wi-Fi that broadcast 'TRESNINOS_DISCOVER' (UDP 8765) with this server's port,
    so the phone app only needs a username and password. (The phone also scans the Wi-Fi if UDP is blocked.)"""
    def run():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", DISCOVERY_PORT))
        except OSError as e:
            E.log(f"[server] Wi-Fi discovery is off (UDP {DISCOVERY_PORT}: {e}); phones will scan for the PC instead")
            return
        s.settimeout(1.0)
        answer = json.dumps({"app": APP_ID, "port": SERVE_PORT, "server": socket.gethostname()}).encode()
        while not SERVICE.stopping.is_set():
            try:
                data, addr = s.recvfrom(256)
            except (socket.timeout, OSError):
                continue
            if data.strip() == b"TRESNINOS_DISCOVER":
                try:
                    s.sendto(answer, addr)
                except OSError:
                    pass
        s.close()
    threading.Thread(target=run, daemon=True, name="discovery").start()


# =====================================================================
# THE API
# =====================================================================
@asynccontextmanager
async def lifespan(_app):
    for d in (UPLOAD_DIR, THUMB_DIR, AVATAR_DIR):
        d.mkdir(parents=True, exist_ok=True)
    init_server_db()
    SERVICE.start()
    start_discovery()
    start_hotspot_watch()
    yield
    SERVICE.stop()


app = FastAPI(title="Shop OCR API", version="1.0", lifespan=lifespan,
              description="API used by the phone app to send receipt photos to the shop PC.")


# the React desktop app (Vite dev server / packaged window) calls this API from another origin
app.add_middleware(CORSMiddleware, allow_origins=["null"],
                   allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",   # any local port (Vite 5173, 5174...)
                   allow_methods=["*"], allow_headers=["*"])
def reauth(request, conn, user, system_password, username, password):
    """Dangerous actions (Transfer -> Delete all records): the system password AND the signed-in person's own username
    and password again. Wrong answers count toward the same 5-tries-in-5-minutes lock as the sign-in screen."""
    too_many_fails(request)
    stored = shop_db.get_setting(conn, "system_password")
    u = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1", ((username or "").strip(),)).fetchone()
    ok = bool(stored) and check_password(system_password or "", stored) and u is not None and u["id"] == user["id"] \
        and check_password(password or "", u["password_hash"])
    if not ok:
        note_fail(request)
        # 403, not 401: a 401 would sign this person out of the desktop app
        raise HTTPException(403, "The system password, the username or the password is wrong (type YOUR OWN username).")


def read_again(rid, path):
    """Desktop -> a receipt -> Read again: back into the reader's queue (same as a new photo)."""
    SERVICE.submit(rid, path)


SHOP_ROUTER = shop_api.build_router(current_user, get_db, AVATAR_DIR, UPLOAD_DIR, THUMB_DIR, save_avatar, hash_password,
                                    require_desk, reauth=reauth, reread=read_again, drop_receipt=lambda conn, r, revert: remove_receipt(conn, r, revert))
app.include_router(SHOP_ROUTER)


class LoginIn(BaseModel):
    username: str
    password: str


class LinePatch(BaseModel):
    item_id: Optional[int] = None
    qty: Optional[float] = None


class LineAdd(BaseModel):
    item_id: int
    qty: float


class CommitIn(BaseModel):
    skip_short: bool = False     # sell only the items that are fully in stock
    partial: bool = False        # sell what we HAVE of short items (asked 100 kg, have 50 kg -> sell 50 kg)
    pay_mode: Optional[str] = None   # 'cash' | 'bank': how the customer paid, asked on the phone (round 17)


class NewItemIn(BaseModel):      # the phone: "send it to the owner as a new item"
    name: str
    price: float
    qty: float
    unit: str = ""
    stock: Optional[float] = None    # how many are left in the shop after this sale (the owner approves it); None = not said


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request):
    if WEB_DIR and is_local(request):        # the packaged app: the desktop window opens this address
        return FileResponse(WEB_DIR / "index.html")
    st = SERVICE.status()
    return (f"<h2>Shop OCR server is running</h2><p>OCR ready: <b>{st['ready']}</b> ({st['device']}, {st['model']}), "
            f"queue: {st['queued']}</p><p>Phone app address: this page's address.<br>"
            f"API documentation: <a href='/docs'>/docs</a></p>")


def system_password_set(conn):
    return bool(shop_db.get_setting(conn, "system_password"))


@app.get("/api/ping", tags=["status"])
def ping(conn=Depends(get_db)):
    """No login needed. Phones use it to recognise the shop PC (app = tres-ninos-shop).
    needs_setup = no system password yet (first run of the desktop app)."""
    return {"ok": True, "app": APP_ID, "server": socket.gethostname(), "time": now_iso(), "version": SERVER_VERSION,
            "needs_setup": not system_password_set(conn), **SERVICE.status()}


class SetupIn(BaseModel):
    system_password: str
    username: str
    password: str
    display_name: Optional[str] = None       # ignored: the username is the name


def new_session(conn, u, kind="phone"):
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires < ?", (now_iso(),))
    conn.execute("INSERT INTO sessions(token_hash, user_id, created, expires, last_seen, kind) VALUES(?,?,?,?,?,?)",
                 (token_hash(token), u["id"], now_iso(),
                  (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"), now_iso(), kind))
    return {"token": token, "user": {"id": u["id"], "username": u["username"],
                                     "display_name": u["display_name"] or u["username"]}}


def create_desk_user(conn, body):
    if not body.username.strip() or len(body.password) < 4:
        raise HTTPException(422, "Username and a password of at least 4 characters are required")
    try:
        uid = conn.execute("INSERT INTO users(username, display_name, password_hash, is_admin, created) VALUES(?,?,?,1,?)",
                           (body.username.strip(), body.username.strip(),
                            hash_password(body.password), now_iso())).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That username is already taken. Pick another one (or sign in if it is yours).")
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


@app.post("/api/setup", tags=["account"])
def setup(body: SetupIn, request: Request, conn=Depends(get_db)):
    """FIRST RUN: choose the shop's system password and make your own desktop account (signed in straight away).
    If the username already exists as a desktop account (made before system passwords existed), its password must match.
    Refused (409) once a system password exists - use /api/register."""
    require_local(request)
    if system_password_set(conn):
        raise HTTPException(409, "The system password is already set. Use 'New person' or sign in.")
    if len(body.system_password) < 4:
        raise HTTPException(422, "The system password needs at least 4 characters")
    with conn:
        u = conn.execute("SELECT * FROM users WHERE username=?", (body.username.strip(),)).fetchone()
        if u:
            if not u["is_admin"] or not check_password(body.password, u["password_hash"]):
                raise HTTPException(409, "That username is already taken. Pick another one.")
        else:
            u = create_desk_user(conn, body)
        shop_db.set_setting(conn, "system_password", hash_password(body.system_password))
        shop_db.audit(conn, u["id"], "System password set", "first setup")
        shop_db.audit(conn, u["id"], "Signed in", "")
        return new_session(conn, u, "desk")


@app.post("/api/register", tags=["account"])
def register(body: SetupIn, request: Request, conn=Depends(get_db)):
    """'New person' on the desktop sign-in screen: whoever knows the SYSTEM PASSWORD makes their own username + password
    and is signed in. (Phone logins for sales people are still made in Cash Flow -> Employee -> Phone login.)"""
    require_local(request)
    too_many_fails(request)
    stored = shop_db.get_setting(conn, "system_password")
    if not stored:
        raise HTTPException(409, "No system password yet. Use the first-run setup.")
    if not check_password(body.system_password, stored):
        note_fail(request)
        raise HTTPException(401, "Wrong system password")
    with conn:
        u = create_desk_user(conn, body)
        shop_db.audit(conn, u["id"], "New account", f"{u['username']} made an account with the system password")
        shop_db.audit(conn, u["id"], "Signed in", "")
        return new_session(conn, u, "desk")


# =====================================================================
# PHONES CANNOT CONNECT? (Windows network type + firewall) - shown on the desktop, fixed with one click
# =====================================================================
_NET = {"t": 0.0, "nets": None}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def windows_networks(fresh=False):
    """The networks Windows is connected to and whether each is Private or Public (None when not on Windows).
    Public = Windows hides this laptop there and the firewall rule for the shop program does not apply."""
    if sys.platform != "win32":
        return None
    if not fresh and _NET["nets"] is not None and time.time() - _NET["t"] < 20:
        return _NET["nets"]
    ps = ("Get-NetConnectionProfile | Select-Object Name, InterfaceAlias, InterfaceIndex, "
          "@{n='Category';e={$_.NetworkCategory.ToString()}}, @{n='Connectivity';e={$_.IPv4Connectivity.ToString()}} "
          "| ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, timeout=20, creationflags=NO_WINDOW)
        data = json.loads(out.stdout or "[]")
    except Exception:
        return _NET["nets"] or []
    data = [data] if isinstance(data, dict) else data
    nets = [{"name": d.get("Name") or d.get("InterfaceAlias"), "alias": d.get("InterfaceAlias"), "category": d.get("Category"),
             "connectivity": d.get("Connectivity")} for d in data if d.get("Connectivity") != "Disconnected"]
    _NET.update(t=time.time(), nets=nets)
    return nets


_FW = {"t": 0.0, "v": None}


def firewall_state(fresh=False):
    """Which Windows Firewall rules let phones in (inbound) for THIS program or port 8000, per network type.
    Returns {"allow": [profiles], "block": [profiles]} (profile names like "Private", "Public", "Domain", "Any"),
    or None when not on Windows / it could not be read. A BLOCK rule wins over any allow rule: Windows makes one when
    its 'Allow access?' question on the first start was cancelled or closed."""
    if sys.platform != "win32":
        return None
    if not fresh and _FW["v"] is not None and time.time() - _FW["t"] < 60:
        return _FW["v"]
    q = lambda x: str(x).replace("'", "''")
    ps = (f"$exe = '{q(sys.executable)}'; "
          "$app = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -ieq $exe } | "
          "Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' }); "
          f"$port = @(Get-NetFirewallPortFilter -Protocol TCP -ErrorAction SilentlyContinue | Where-Object {{ $_.LocalPort -eq '{SERVE_PORT}' }} | "
          "Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' "
          "-and $_.DisplayName -notlike '*(phone Wi-Fi*' }); "
          "$all = @($app) + @($port); "
          "[pscustomobject]@{ allow = @($all | Where-Object { $_.Action -eq 'Allow' } | ForEach-Object { $_.Profile.ToString() }); "
          "block = @($app | Where-Object { $_.Action -eq 'Block' } | ForEach-Object { $_.Profile.ToString() }) } | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, timeout=40, creationflags=NO_WINDOW)
        d = json.loads(out.stdout or "null") or {}
    except Exception:
        return _FW["v"]
    split = lambda xs: sorted({p.strip() for x in (xs or []) for p in str(x).split(",") if p.strip()})
    v = {"allow": split(d.get("allow")), "block": split(d.get("block"))}
    _FW.update(t=time.time(), v=v)
    return v


def phone_problems(nets, fw):
    """Plain-language reasons why phones cannot reach this laptop right now ([] = nothing found)."""
    out = []
    for n in nets or []:
        cat = {"DomainAuthenticated": "Domain"}.get(n["category"], n["category"])
        if cat == "Public":
            out.append({"kind": "public", "network": n["name"],
                        "text": f"Windows treats the Wi-Fi “{n['name']}” as a Public network, so it blocks the phones."})
            continue
        if fw is None:
            continue
        covers = lambda lst: any(p in ("Any", cat) for p in lst)
        if covers(fw["block"]):
            out.append({"kind": "blocked", "network": n["name"],
                        "text": "Windows Firewall has a rule that BLOCKS this program (made when its “Allow access?” "
                                "question was cancelled), so phones cannot connect."})
        elif not covers(fw["allow"]):
            out.append({"kind": "noallow", "network": n["name"],
                        "text": f"Windows Firewall has not allowed this program on {cat} networks yet, so phones cannot connect."})
    return out


@app.get("/api/shop/network", tags=["shop (owner)"])
def network_status(request: Request, fresh: bool = False, user=Depends(current_user)):
    """Where phones should connect (this laptop's addresses) and what in Windows stops them (network type, firewall)."""
    require_desk(request, user)
    nets = windows_networks(fresh)
    fw = firewall_state(fresh)
    problems = phone_problems(nets, fw)
    return {"addresses": lan_addresses(), "port": SERVE_PORT, "windows": nets is not None, "networks": nets or [],
            "firewall": fw, "problems": problems, "firewallUnknown": nets is not None and fw is None,
            "public": [n["name"] for n in (nets or []) if n["category"] == "Public"]}


def allow_phones():
    """Set the network(s) this laptop is on to PRIVATE and allow the shop program (port 8000 and the phone finder on
    UDP 8765) through the firewall on Private networks. Windows asks for permission (Yes). Returns the problems left
    ([] = phones can connect). Raises PermissionError when the Windows question was answered No,
    TimeoutError when it was not answered. Used by the Fix button and by the .exe on its first start on a laptop."""
    q = lambda x: str(x).replace("'", "''")
    script = E.APP_DIR / "fix_network.ps1"
    script.write_text("\r\n".join([
        "$ErrorActionPreference = 'Continue'",
        "Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' -and $_.IPv4Connectivity -ne 'Disconnected' } |"
        " ForEach-Object { Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private }",
        # the block rules Windows made when its "Allow access?" question was cancelled (they win over any allow rule)
        f"Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object {{ $_.Program -ieq '{q(sys.executable)}' }} | Get-NetFirewallRule"
        " -ErrorAction SilentlyContinue | Where-Object { $_.Action -eq 'Block' } | Remove-NetFirewallRule -ErrorAction SilentlyContinue",
        "foreach ($n in 'Tres Ninos Shop (app)','Tres Ninos Shop (port 8000)','Tres Ninos Shop (phone finder)',"
        "'Tres Ninos Shop (phone Wi-Fi)','Tres Ninos Shop (phone Wi-Fi finder)') {"
        " Remove-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue }",
        f"New-NetFirewallRule -DisplayName 'Tres Ninos Shop (app)' -Direction Inbound -Action Allow -Profile Private,Domain -Program '{q(sys.executable)}' | Out-Null",
        f"New-NetFirewallRule -DisplayName 'Tres Ninos Shop (port 8000)' -Direction Inbound -Action Allow -Profile Private,Domain -Protocol TCP -LocalPort {SERVE_PORT} | Out-Null",
        f"New-NetFirewallRule -DisplayName 'Tres Ninos Shop (phone finder)' -Direction Inbound -Action Allow -Profile Private,Domain -Protocol UDP -LocalPort {DISCOVERY_PORT} | Out-Null",
        # the phones on this laptop's own Wi-Fi (Mobile Hotspot): only that network's addresses, on any network type
        f"New-NetFirewallRule -DisplayName 'Tres Ninos Shop (phone Wi-Fi)' -Direction Inbound -Action Allow -Profile Any -Protocol TCP -LocalPort {SERVE_PORT} -RemoteAddress {HOTSPOT_NET} | Out-Null",
        f"New-NetFirewallRule -DisplayName 'Tres Ninos Shop (phone Wi-Fi finder)' -Direction Inbound -Action Allow -Profile Any -Protocol UDP -LocalPort {DISCOVERY_PORT} -RemoteAddress {HOTSPOT_NET} | Out-Null",
    ]), encoding="utf-8-sig")              # with BOM: Windows PowerShell 5 reads it correctly
    cmd = (f"$p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
           f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"{q(script)}\"'; exit $p.ExitCode")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=180,
                             creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise TimeoutError("no answer")
    finally:
        script.unlink(missing_ok=True)
    if out.returncode != 0 and "cancel" in (out.stderr or "").lower():
        raise PermissionError("cancelled")
    return phone_problems(windows_networks(fresh=True), firewall_state(fresh=True))


@app.post("/api/shop/network/fix", tags=["shop (owner)"])
def network_fix(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    """The Fix button: allow_phones(). Windows asks for permission (press Yes)."""
    require_desk(request, user)
    if sys.platform != "win32":
        raise HTTPException(400, "Only needed on Windows")
    try:
        problems = allow_phones()
    except TimeoutError:
        raise HTTPException(408, "Windows did not answer. Try again and press Yes on the Windows question.")
    except PermissionError:
        raise HTTPException(409, "Cancelled: Windows asked for permission and it was not given (press Yes next time).")
    nets = windows_networks(fresh=True)
    with conn:
        shop_db.audit(conn, user["id"], "Network", "set this Wi-Fi to Private and allowed the shop program for phones")
    if problems:
        raise HTTPException(409, problems[0]["text"] + " Windows did not accept the change; this laptop's settings may "
                                 "be locked by your organization (ask whoever manages it to allow TresNinosShop on port 8000).")
    return {"ok": True, "networks": nets}


# =====================================================================
# THE PHONES' OWN WI-FI: Windows Mobile Hotspot made by this laptop
# When the shop Wi-Fi (an extender...) does not let devices reach each other, the laptop makes its own Wi-Fi.
# The phones join it once (Android then joins it by itself), find the laptop at 192.168.137.1, and still get
# internet through the laptop. "Turn on by itself" keeps it on: Windows switches it off after 5 minutes without
# phones, so a watcher turns it on again every minute.
# =====================================================================
HOTSPOT_NET = "192.168.137.0/24"
_HOTSPOT_LOCK = threading.Lock()

_HOTSPOT_PS = r"""
$ErrorActionPreference = 'Stop'
function Out-Json($h) { $h | ConvertTo-Json -Compress; exit 0 }
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $m1 = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
  $m0 = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
  function Await($op, [Type]$t) { $task = $m1.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
  function AwaitAction($op) { $task = $m0.Invoke($null, @($op)); $task.Wait(-1) | Out-Null }
  [void][Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
  [void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
  [void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]
  $TM = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]
  $p = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
  if (-not $p) { $p = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles() | Where-Object { $_.IsWlanConnectionProfile -or $_.IsWwanConnectionProfile } | Select-Object -First 1 }
  if (-not $p) { $p = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles() | Select-Object -First 1 }
  if (-not $p) { Out-Json @{ ok = $false; supported = $true; error = 'no_network' } }
  $cap = $TM::GetTetheringCapabilityFromConnectionProfile($p)
  if ([int]$cap -ne 0) { Out-Json @{ ok = $false; supported = $false; error = $cap.ToString() } }
  $m = $TM::CreateFromConnectionProfile($p)
  $act = $env:TN_ACTION
  if ($act -eq 'start') {
    $c = $m.GetCurrentAccessPointConfiguration()
    if ($c.Ssid -ne $env:TN_SSID -or $c.Passphrase -ne $env:TN_PASS) {
      if ([int]$m.TetheringOperationalState -eq 1) { [void](Await ($m.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])) }
      $c.Ssid = $env:TN_SSID; $c.Passphrase = $env:TN_PASS
      AwaitAction ($m.ConfigureAccessPointAsync($c))
    }
    if ([int]$m.TetheringOperationalState -ne 1) {
      $r = Await ($m.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
      if ([int]$r.Status -ne 0) { Out-Json @{ ok = $false; supported = $true; error = $r.Status.ToString(); message = $r.AdditionalErrorMessage } }
    }
  } elseif ($act -eq 'stop') {
    if ([int]$m.TetheringOperationalState -eq 1) { [void](Await ($m.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])) }
  }
  $c = $m.GetCurrentAccessPointConfiguration()
  Out-Json @{ ok = $true; supported = $true; on = ([int]$m.TetheringOperationalState -eq 1); ssid = $c.Ssid; clients = $m.ClientCount; max = $m.MaxClientCount; via = $p.ProfileName }
} catch {
  Out-Json @{ ok = $false; supported = $true; error = 'failed'; message = $_.Exception.Message }
}
"""

HOTSPOT_ERRORS = {
    "no_network": "This laptop is not connected to any network. Connect it to the Wi-Fi first.",
    "WiFiDeviceOff": "The laptop's Wi-Fi is off. Turn it on (Windows: the Wi-Fi button), then try again.",
    "DisabledByGroupPolicy": "This laptop's administrator does not allow a Mobile Hotspot.",
    "DisabledByHardwareLimitation": "This laptop's Wi-Fi card cannot make a hotspot while it is connected to this Wi-Fi.",
    "DisabledByOperator": "The Mobile Hotspot is not allowed on this connection.",
    "DisabledBySku": "This edition of Windows has no Mobile Hotspot.",
    "DisabledByRequiredAppNotInstalled": "Windows is missing a part needed for the Mobile Hotspot.",
    "DisabledDueToUnknownCause": "Windows does not allow a Mobile Hotspot on this connection.",
    "DisabledBySystemCapability": "This laptop's Wi-Fi card cannot make a Mobile Hotspot.",
    "WiFiDeviceOffline": "The laptop's Wi-Fi card is not ready. Turn Wi-Fi off and on, then try again.",
    "EntitlementCheckFailure": "Windows could not start the Mobile Hotspot on this connection.",
    "EntitlementCheckTimeout": "Windows could not start the Mobile Hotspot on this connection.",
    "OperationInProgress": "Windows is still switching the Mobile Hotspot. Wait a few seconds.",
}


def hotspot_call(action="status", ssid="", password=""):
    """Run the Mobile Hotspot script: status / start / stop. Returns the dict it printed (never raises)."""
    if sys.platform != "win32":
        return {"ok": False, "supported": False, "error": "not_windows"}
    with _HOTSPOT_LOCK:
        try:
            enc = __import__("base64").b64encode(_HOTSPOT_PS.encode("utf-16-le")).decode()     # no quoting problems
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                                 capture_output=True, text=True, timeout=90, creationflags=NO_WINDOW,
                                 env={**os.environ, "TN_ACTION": action, "TN_SSID": ssid, "TN_PASS": password})
            d = json.loads((out.stdout or "").strip().splitlines()[-1]) if (out.stdout or "").strip() else {}
        except Exception as e:
            return {"ok": False, "supported": True, "error": "failed", "message": str(e)}
    if not d:
        return {"ok": False, "supported": True, "error": "failed", "message": (out.stderr or "")[-300:]}
    if not d.get("ok"):
        d["text"] = HOTSPOT_ERRORS.get(d.get("error"), "Windows could not switch the Mobile Hotspot"
                                       + (f": {d['message']}" if d.get("message") else "."))
    return d


def hotspot_settings(conn):
    """name + password of the phones' Wi-Fi (made once, kept in the database) and whether it turns on by itself"""
    ssid = shop_db.get_setting(conn, "hotspot_ssid")
    pw = shop_db.get_setting(conn, "hotspot_password")
    if not ssid or not pw:
        ssid = ssid or "TresNinos-Shop"
        pw = pw or f"tresninos{secrets.randbelow(9000) + 1000}"
        with conn:
            shop_db.set_setting(conn, "hotspot_ssid", ssid)
            shop_db.set_setting(conn, "hotspot_password", pw)
    return ssid, pw, shop_db.get_setting(conn, "hotspot_auto") == "1"


def hotspot_firewall_ok():
    """Is the firewall rule for phones on the laptop's own Wi-Fi there? (made by allow_phones)"""
    if sys.platform != "win32":
        return True
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "@(Get-NetFirewallRule -DisplayName 'Tres Ninos Shop (phone Wi-Fi)' -ErrorAction SilentlyContinue "
                              "| Where-Object { $_.Enabled -eq 'True' }).Count"],
                             capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        return (out.stdout or "0").strip() not in ("", "0")
    except Exception:
        return True


def start_hotspot_watch():
    """'Turn on by itself': when the program starts and every minute after, the phones' Wi-Fi is switched on again if
    it is off (Windows turns it off after 5 minutes with no phone connected)."""
    if sys.platform != "win32":
        return

    def run():
        first = True
        while not SERVICE.stopping.is_set():
            try:
                conn = db()
                try:
                    auto = shop_db.get_setting(conn, "hotspot_auto") == "1"
                    if auto:
                        ssid, pw, _ = hotspot_settings(conn)
                finally:
                    conn.close()
                if auto:
                    st = hotspot_call("status")
                    if st.get("ok") and not st.get("on"):
                        r = hotspot_call("start", ssid, pw)
                        if first or not r.get("ok"):
                            E.log(f"[server] phones' Wi-Fi {ssid}: " + ("on" if r.get("ok") else r.get("text", "could not start")))
            except Exception as e:
                E.log(f"[server] phones' Wi-Fi watch: {e}")
            first = False
            SERVICE.stopping.wait(60)
    threading.Thread(target=run, daemon=True, name="hotspot-watch").start()


class HotspotIn(BaseModel):
    on: bool
    auto: Optional[bool] = None
    ssid: Optional[str] = None
    password: Optional[str] = None


@app.get("/api/shop/hotspot", tags=["shop (owner)"])
def hotspot_get(request: Request, user=Depends(current_user), conn=Depends(get_db)):
    """The phones' own Wi-Fi: on / off, its name and password, how many phones are on it."""
    require_desk(request, user)
    ssid, pw, auto = hotspot_settings(conn)
    st = hotspot_call("status")
    return {"windows": sys.platform == "win32", "supported": st.get("supported", False), "on": bool(st.get("on")),
            "clients": st.get("clients", 0), "max": st.get("max"), "via": st.get("via"), "ssid": ssid, "password": pw,
            "auto": auto, "error": None if st.get("ok") else st.get("text"), "address": "192.168.137.1"}


@app.post("/api/shop/hotspot", tags=["shop (owner)"])
def hotspot_set(body: HotspotIn, request: Request, user=Depends(current_user), conn=Depends(get_db)):
    """Turn the phones' Wi-Fi on / off (and 'turn on by itself'), optionally with a new name or password.
    The first time it is turned on, the firewall rule for it is made (Windows asks: press Yes)."""
    require_desk(request, user)
    if sys.platform != "win32":
        raise HTTPException(400, "Only on Windows")
    ssid, pw, auto = hotspot_settings(conn)
    if body.ssid is not None:
        s = body.ssid.strip()
        if not 1 <= len(s.encode("utf-8")) <= 32:
            raise HTTPException(422, "The Wi-Fi name must be 1 to 32 letters.")
        ssid = s
    if body.password is not None:
        if not 8 <= len(body.password) <= 63 or not body.password.isascii():
            raise HTTPException(422, "The Wi-Fi password must be 8 to 63 letters or numbers.")
        pw = body.password
    with conn:
        shop_db.set_setting(conn, "hotspot_ssid", ssid)
        shop_db.set_setting(conn, "hotspot_password", pw)
        if body.auto is not None:
            shop_db.set_setting(conn, "hotspot_auto", "1" if body.auto else "0")
        elif not body.on:
            shop_db.set_setting(conn, "hotspot_auto", "0")         # turned off by hand: do not turn it on again
    if body.on and not hotspot_firewall_ok():
        try:
            allow_phones()
        except PermissionError:
            raise HTTPException(409, "Cancelled: Windows asked for permission and it was not given (press Yes next time).")
        except TimeoutError:
            raise HTTPException(408, "Windows did not answer. Try again and press Yes on the Windows question.")
    r = hotspot_call("start" if body.on else "stop", ssid, pw)
    if not r.get("ok"):
        raise HTTPException(409, r.get("text") or "Windows could not switch the Mobile Hotspot.")
    with conn:
        shop_db.audit(conn, user["id"], "Network", f"phones' Wi-Fi {ssid} turned {'on' if body.on else 'off'}"
                      + (" (turns on by itself)" if body.on and (body.auto if body.auto is not None else auto) else ""))
    return {"ok": True, "on": bool(r.get("on")), "ssid": ssid, "password": pw, "clients": r.get("clients", 0)}


@app.get("/api/desk-avatar/{username}", include_in_schema=False)
def desk_avatar(username: str, request: Request, conn=Depends(get_db)):
    """The sign-in screen shows the person's picture as soon as their username is typed (this laptop only)."""
    require_local(request)
    # same picture as everywhere else: the account's own photo, else the photo of the employee linked to it / same name
    row = conn.execute("""SELECT u.avatar AS a, COALESCE(e.photo,
                                 (SELECT photo FROM employees x WHERE lower(x.name)=lower(COALESCE(u.display_name,u.username))
                                  AND x.photo IS NOT NULL LIMIT 1)) AS p
                          FROM users u LEFT JOIN employees e ON e.user_id=u.id
                          WHERE u.username=? AND u.active=1""", (username.strip(),)).fetchone()
    for name in (row["a"], row["p"]) if row else ():
        if name and (AVATAR_DIR / name).exists():
            return FileResponse(AVATAR_DIR / name, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    raise HTTPException(404, "No picture")


class DeskLoginIn(BaseModel):
    system_password: str
    username: str
    password: str


@app.post("/api/desk-login", tags=["account"])
def desk_login(body: DeskLoginIn, request: Request, conn=Depends(get_db)):
    """Desktop sign-in: the SYSTEM PASSWORD + your own username and password. Only on the shop laptop itself."""
    require_local(request)
    too_many_fails(request)
    stored = shop_db.get_setting(conn, "system_password")
    if not stored:
        raise HTTPException(409, "No system password yet. Use the first-run setup.")
    if not check_password(body.system_password, stored):
        note_fail(request)
        raise HTTPException(401, "Wrong system password")
    u = conn.execute("SELECT * FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
    if not u or not check_password(body.password, u["password_hash"]):
        note_fail(request)
        raise HTTPException(401, "Wrong username or password")
    if not u["active"]:
        raise HTTPException(403, "This account is disabled.")
    if not u["is_admin"]:
        raise HTTPException(403, "This is a phone login. To use this desktop, press 'New person' and make a desktop account.")
    with conn:
        out = new_session(conn, u, "desk")
        shop_db.audit(conn, u["id"], "Signed in", "")
    return out


class SysPassIn(BaseModel):
    old: str
    new: str


@app.post("/api/system-password", tags=["account"])
def change_system_password(body: SysPassIn, request: Request, authorization: Optional[str] = Header(None),
                           user=Depends(current_user), conn=Depends(get_db)):
    """Change the system password (desktop accounts only). Accounts keep working with the new system password;
    every OTHER desktop session is signed out (someone who left can no longer get in)."""
    require_desk(request, user)
    too_many_fails(request)
    stored = shop_db.get_setting(conn, "system_password")
    if stored and not check_password(body.old, stored):
        note_fail(request)                       # 403, not 401: a 401 would sign this person out of the desktop app
        raise HTTPException(403, "The current system password is wrong")
    if len(body.new) < 4:
        raise HTTPException(422, "The new system password needs at least 4 characters")
    with conn:
        shop_db.set_setting(conn, "system_password", hash_password(body.new))
        conn.execute("DELETE FROM sessions WHERE kind='desk' AND token_hash<>?", (token_hash(authorization[7:].strip()),))
        shop_db.audit(conn, user["id"], "System password changed", "other desktop sessions signed out")
    return {"ok": True}


@app.post("/api/login", tags=["account"])
def login(body: LoginIn, request: Request, conn=Depends(get_db)):
    """The phone app's 'Verify' button. Checks the account on the PC and returns a token for the phone calls.
    (The desktop signs in with /api/desk-login, which also needs the system password.)"""
    too_many_fails(request)
    u = conn.execute("SELECT * FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
    if not u or not check_password(body.password, u["password_hash"]):
        note_fail(request)
        raise HTTPException(401, "Wrong username or password")
    if not u["active"]:
        raise HTTPException(403, "This account is disabled. Ask the shop owner.")
    with conn:
        out = new_session(conn, u, "phone")
    return out


@app.post("/api/logout", tags=["account"])
def logout(authorization: Optional[str] = Header(None), user=Depends(current_user), conn=Depends(get_db)):
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(authorization[7:].strip()),))
        if user["is_admin"]:
            shop_db.audit(conn, user["id"], "Signed out", "")
    return {"ok": True}


def avatar_file(conn, user):
    """The person's picture: their own photo, else the photo of the employee linked to the login (Cash Flow), else an
    employee with the same name. (Before, only the first one counted, so a phone login made AFTER the employee photo
    was uploaded showed no picture on the phone.)"""
    names = [user["avatar"]]
    row = conn.execute("""SELECT e.photo FROM employees e WHERE e.user_id=? AND e.photo IS NOT NULL
                          UNION ALL SELECT photo FROM employees WHERE lower(name)=lower(?) AND photo IS NOT NULL""",
                       (user["id"], user["display_name"] or user["username"])).fetchall()
    names += [r[0] for r in row]
    for n in names:
        if n and (AVATAR_DIR / n).is_file():
            return AVATAR_DIR / n
    return None


@app.get("/api/me", tags=["account"])
def me(user=Depends(current_user), conn=Depends(get_db)):
    return {"id": user["id"], "username": user["username"], "display_name": user["display_name"] or user["username"],
            "has_avatar": avatar_file(conn, user) is not None, "active": True}


@app.get("/api/me/avatar", tags=["account"])
def my_avatar(user=Depends(current_user), conn=Depends(get_db)):
    path = avatar_file(conn, user)
    if not path:
        raise HTTPException(404, "No profile picture")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/items", tags=["inventory"])
def items(user=Depends(current_user), conn=Depends(get_db)):
    """The product list (used by the 'edit row' drop-down). stock_known=false: the stock was never counted (N/A)."""
    return [{"id": i["id"], "name": i["name"], "unit": i["unit"], "quantity": i["quantity"], "sell_price": i["sell_price"],
             "stock_known": not E.qty_unknown(i)} for i in E.get_items(conn)]


@app.get("/api/units", tags=["inventory"])
def unit_names(user=Depends(current_user), conn=Depends(get_db)):
    """Unit names for the phone's "new item" form (pcs, bags, kg, liters...)."""
    try:
        return [u["name"] for u in conn.execute("SELECT name FROM units ORDER BY id")]
    except sqlite3.OperationalError:
        return ["pcs", "bags", "kg", "liters", "meters"]


@app.get("/api/stats", tags=["sales"])
def stats(range: str = "today", user=Depends(current_user), conn=Depends(get_db)):
    """My sales total. range = today | week (last 7 days) | month (last 30 days)."""
    now = datetime.now()
    if range == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range == "week":
        start = now - timedelta(days=7)
    elif range == "month":
        start = now - timedelta(days=30)
    else:
        raise HTTPException(422, "range must be today, week or month")
    r = conn.execute("SELECT COALESCE(SUM(total),0) AS t, COUNT(*) AS n FROM sales WHERE user_id=? AND created >= ?",
                     (user["id"], start.isoformat(timespec="seconds"))).fetchone()
    return {"range": range, "from": start.isoformat(timespec="seconds"), "total": r["t"], "count": r["n"]}


# ---------------- receipts ----------------
def own_receipt(conn, rid, user):
    r = conn.execute("SELECT * FROM receipts WHERE id=? AND user_id=?", (rid, user["id"])).fetchone()
    if not r:
        raise HTTPException(404, "Receipt not found")
    return r


def ui_state(status, n_lines, n_sold):
    if status != "done":
        return status
    if n_lines and n_sold == n_lines:
        return "sold"
    return "partial" if n_sold else "ready"


def summary(r, n_lines, n_sold):
    return {"id": r["id"], "status": r["status"], "state": ui_state(r["status"], n_lines, n_sold), "error": r["error"],
            "created": r["created"], "n_lines": n_lines, "n_sold": n_sold, "seconds": r["seconds"],
            "image_url": f"/api/receipts/{r['id']}/image"}


def detail(conn, r):
    rows = conn.execute("""SELECT l.*, i.name AS item_name, i.unit, i.quantity AS stock, i.sell_price, i.qty_unknown
                           FROM receipt_lines l JOIN items i ON i.id = l.item_id
                           WHERE l.receipt_id=? ORDER BY l.id""", (r["id"],)).fetchall()
    status = E.annotate_lines(conn, [{"item_id": x["item_id"], "qty": x["qty"], "score": x["score"] or 0,
                                      "verified": bool(x["verified"]), "sold": bool(x["sold"])} for x in rows])
    need = {}                                   # all unsold rows of one item together (what "short" is checked on)
    for x in rows:
        if not x["sold"] and x["qty"]:
            need[x["item_id"]] = need.get(x["item_id"], 0.0) + x["qty"]
    lines, order_total = [], 0.0
    for x, st in zip(rows, status):
        total = (x["qty"] or 0) * x["sell_price"]
        if st in ("ok", "check"):
            order_total += total
        lines.append({"id": x["id"], "ocr_text": x["ocr_text"], "item_id": x["item_id"], "item_name": x["item_name"],
                      "unit": x["unit"], "qty": x["qty"], "score": round(x["score"] or 0), "stock": x["stock"],
                      "stock_known": not x["qty_unknown"], "need": round(need.get(x["item_id"], 0.0), 3),
                      "status": st, "line_total": total, "sold": bool(x["sold"])})
    n_sold = sum(1 for x in rows if x["sold"])
    out = summary(r, len(rows), n_sold)
    unmatched = []
    for x in conn.execute("SELECT * FROM receipt_unmatched WHERE receipt_id=? ORDER BY id", (r["id"],)):
        req = None
        if x["req_at"]:                         # sent to the owner as a new Supply item: waiting for the owner
            req = {"name": x["req_name"], "price": x["req_price"], "qty": x["req_qty"], "unit": x["req_unit"] or "",
                   "stock": x["req_stock"], "at": x["req_at"]}
        unmatched.append({"id": x["id"], "ocr_text": x["ocr_text"], "item": x["item_text"], "qty": x["qty"],
                          "price": x["price"], "unit": x["unit"] or "", "request": req})
    out.update({"lines": lines, "order_total": order_total, "unmatched": unmatched,
                "payment": {"mode": r["pay_mode"], "why": r["pay_note"] or "", "sure": bool(r["pay_sure"])}})
    return out


@app.post("/api/receipts", status_code=202, tags=["receipts"])
def upload(image: UploadFile = File(...), client_id: Optional[str] = Form(None),
           user=Depends(current_user), conn=Depends(get_db)):
    """Phone sends a photo (multipart form: image=<file>, client_id=<random id made by the phone>).
    Returns at once; the OCR runs in the background. Sending the same client_id again does NOT create a duplicate."""
    if client_id:
        old = conn.execute("SELECT * FROM receipts WHERE user_id=? AND client_id=?", (user["id"], client_id)).fetchone()
        if old:
            return {"id": old["id"], "status": old["status"], "duplicate": True}
    data = image.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Image is larger than {MAX_UPLOAD_MB} MB")
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")   # bake in the phone's rotation
        im.thumbnail((2400, 2400))
    except Exception:
        raise HTTPException(422, "That file is not a readable image")
    try:
        with conn:
            cur = conn.execute("INSERT INTO receipts(user_id, client_id, image, status, created) VALUES(?,?,?,?,?)",
                               (user["id"], client_id, "", "queued", now_iso()))
    except sqlite3.IntegrityError:               # the phone re-sent the same photo while the first send was still busy
        old = conn.execute("SELECT * FROM receipts WHERE user_id=? AND client_id=?", (user["id"], client_id)).fetchone()
        if old:
            return {"id": old["id"], "status": old["status"], "duplicate": True}
        raise
    with conn:
        rid = cur.lastrowid
        path = UPLOAD_DIR / f"{rid}.jpg"
        im.save(path, "JPEG", quality=90)
        conn.execute("UPDATE receipts SET image=? WHERE id=?", (str(path), rid))
    SERVICE.submit(rid, str(path))
    return {"id": rid, "status": "queued", "duplicate": False}


@app.get("/api/receipts", tags=["receipts"])
def list_receipts(limit: int = 40, user=Depends(current_user), conn=Depends(get_db)):
    """My latest photos with their state: queued | processing | failed | ready | partial | sold."""
    rows = conn.execute("""SELECT r.*, (SELECT COUNT(*) FROM receipt_lines WHERE receipt_id=r.id) AS n_lines,
                                  (SELECT COUNT(*) FROM receipt_lines WHERE receipt_id=r.id AND sold=1) AS n_sold
                           FROM receipts r WHERE r.user_id=? ORDER BY r.id DESC LIMIT ?""",
                        (user["id"], max(1, min(limit, 200)))).fetchall()
    return [summary(r, r["n_lines"], r["n_sold"]) for r in rows]


@app.get("/api/receipts/{rid}", tags=["receipts"])
def get_receipt(rid: int, user=Depends(current_user), conn=Depends(get_db)):
    """One photo with the rows the OCR found (item, quantity, status ok/check/short/no_qty/sold)."""
    return detail(conn, own_receipt(conn, rid, user))


@app.get("/api/receipts/{rid}/image", tags=["receipts"])
def receipt_image(rid: int, w: int = 0, user=Depends(current_user), conn=Depends(get_db)):
    """The photo. Add ?w=400 for a small thumbnail."""
    r = own_receipt(conn, rid, user)
    path = Path(r["image"])
    if not path.exists():                    # the data folder was moved (e.g. into the .exe's folder)
        path = UPLOAD_DIR / path.name
    if not path.exists():
        raise HTTPException(404, "Image file is missing")
    if w <= 0:
        return FileResponse(path, media_type="image/jpeg")
    w = max(64, min(w, 1600))
    thumb = THUMB_DIR / f"{rid}_{w}.jpg"
    if not thumb.exists():
        with Image.open(path) as im:
            im.thumbnail((w, w * 3))
            im.save(thumb, "JPEG", quality=80)
    return FileResponse(thumb, media_type="image/jpeg")


@app.post("/api/receipts/{rid}/retry", tags=["receipts"])
def retry_receipt(rid: int, user=Depends(current_user), conn=Depends(get_db)):
    """Read a failed photo again."""
    r = own_receipt(conn, rid, user)
    if r["status"] != "failed":
        raise HTTPException(409, "Only failed receipts can be retried")
    with conn:
        conn.execute("UPDATE receipts SET status='queued', error=NULL WHERE id=?", (rid,))
    SERVICE.submit(rid, r["image"])
    return {"id": rid, "status": "queued"}


def remove_receipt(conn, r, revert=False):
    """Delete one receipt photo, its rows and its picture files (frees disk space). Used by the phone (own receipts)
    and by the owner (Sales -> a counter -> Receipt -> Delete).
    If part of it was already SOLD and revert is False: returns a 409 JSONResponse {"error":"has_sales", "sold_rows",
    "total"}. With revert=True those sales are undone too: the stock goes back and they leave the sales totals.
    A delivery order made from it that is still waiting goes too. All database changes happen in one transaction."""
    rid = r["id"]
    sold = conn.execute("SELECT DISTINCT sale_id FROM receipt_lines WHERE receipt_id=? AND sold=1", (rid,)).fetchall()
    sale_ids = [x["sale_id"] for x in sold if x["sale_id"] is not None]
    n_sold = conn.execute("SELECT COUNT(*) FROM receipt_lines WHERE receipt_id=? AND sold=1", (rid,)).fetchone()[0]
    restored = 0.0
    if sale_ids:
        restored = conn.execute(f"SELECT COALESCE(SUM(total),0) FROM sales WHERE id IN ({','.join('?' * len(sale_ids))})",
                                sale_ids).fetchone()[0]
    if n_sold and not revert:
        return JSONResponse(status_code=409, content={
            "error": "has_sales", "sold_rows": n_sold, "total": restored,
            "message": "This receipt was already sold. Deleting it must undo the sale (stock goes back, total goes down)."})
    with conn:
        for sid in sale_ids:
            for it in conn.execute("SELECT item_id, qty FROM sale_items WHERE sale_id=?", (sid,)).fetchall():
                # (an item whose stock was never counted - N/A - was not taken out either)
                conn.execute("UPDATE items SET quantity = quantity + ? WHERE id=? AND COALESCE(qty_unknown,0)=0",
                             (it["qty"], it["item_id"]))
            conn.execute("DELETE FROM sale_items WHERE sale_id=?", (sid,))
            conn.execute("DELETE FROM sales WHERE id=?", (sid,))
        conn.execute("DELETE FROM receipt_lines WHERE receipt_id=?", (rid,))
        conn.execute("DELETE FROM receipt_unmatched WHERE receipt_id=?", (rid,))
        conn.execute("DELETE FROM delivery_drafts WHERE receipt_id=? AND status='new'", (rid,))
        conn.execute("UPDATE delivery_drafts SET receipt_id=NULL WHERE receipt_id=?", (rid,))
        conn.execute("DELETE FROM receipts WHERE id=?", (rid,))
    SERVICE.forget(rid)
    files = [UPLOAD_DIR / f"{rid}.jpg"] + list(THUMB_DIR.glob(f"{rid}_*.jpg"))
    if r["image"]:
        img = Path(r["image"])
        files += [img if img.is_absolute() else UPLOAD_DIR / img.name, UPLOAD_DIR / img.name]
    for p in files:
        try:
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and p.is_file():
                p.unlink()
        except OSError:
            pass
    return {"deleted": rid, "reverted_sales": len(sale_ids), "restored_total": restored}


@app.delete("/api/receipts/{rid}", tags=["receipts"])
def delete_receipt(rid: int, revert: bool = False, user=Depends(current_user), conn=Depends(get_db)):
    """Delete a photo, its rows and its picture files (frees disk space).
    If part of it was already SOLD, the answer is 409 {"error":"has_sales", "sold_rows", "total"}; send the request
    again with ?revert=true to also undo those sales: the stock is put back and they leave the sales totals.
    All database changes happen in one transaction."""
    return remove_receipt(conn, own_receipt(conn, rid, user), revert)


def editable_line(conn, rid, lid):
    ln = conn.execute("SELECT * FROM receipt_lines WHERE id=? AND receipt_id=?", (lid, rid)).fetchone()
    if not ln:
        raise HTTPException(404, "Row not found")
    if ln["sold"]:
        raise HTTPException(409, "This row was already sold and cannot be changed")
    return ln


def need_item(conn, item_id):
    if conn.execute("SELECT 1 FROM items WHERE id=? AND deleted_at IS NULL", (item_id,)).fetchone() is None:
        raise HTTPException(422, "Unknown item (it may have been deleted from Supply)")


@app.patch("/api/receipts/{rid}/lines/{lid}", tags=["review"])
def edit_line(rid: int, lid: int, body: LinePatch, user=Depends(current_user), conn=Depends(get_db)):
    """The 'Edit' button: fix the item and/or quantity of one row. Returns the whole receipt again."""
    r = own_receipt(conn, rid, user)
    ln = editable_line(conn, rid, lid)
    item_id = body.item_id if body.item_id is not None else ln["item_id"]
    qty = body.qty if body.qty is not None else ln["qty"]
    need_item(conn, item_id)
    if qty is not None and qty <= 0:
        raise HTTPException(422, "Quantity must be greater than 0")
    with conn:
        conn.execute("UPDATE receipt_lines SET item_id=?, qty=?, score=100, verified=1 WHERE id=?", (item_id, qty, lid))
    return detail(conn, r)


@app.post("/api/receipts/{rid}/lines", tags=["review"])
def add_line(rid: int, body: LineAdd, user=Depends(current_user), conn=Depends(get_db)):
    """Add a row the OCR missed."""
    r = own_receipt(conn, rid, user)
    need_item(conn, body.item_id)
    if body.qty <= 0:
        raise HTTPException(422, "Quantity must be greater than 0")
    with conn:
        conn.execute("INSERT INTO receipt_lines(receipt_id, ocr_text, item_id, score, qty, verified) VALUES(?,?,?,?,?,1)",
                     (rid, "(added by hand)", body.item_id, 100, body.qty))
    return detail(conn, r)


@app.delete("/api/receipts/{rid}/lines/{lid}", tags=["review"])
def delete_line(rid: int, lid: int, user=Depends(current_user), conn=Depends(get_db)):
    """Remove a wrong row."""
    r = own_receipt(conn, rid, user)
    editable_line(conn, rid, lid)
    with conn:
        conn.execute("DELETE FROM receipt_lines WHERE id=?", (lid,))
    return detail(conn, r)


def own_unmatched(conn, rid, uid):
    u = conn.execute("SELECT * FROM receipt_unmatched WHERE id=? AND receipt_id=?", (uid, rid)).fetchone()
    if not u:
        raise HTTPException(404, "That row is not on this receipt any more (the owner may have handled it)")
    return u


def new_item_values(body):
    name = re.sub(r"\s+", " ", body.name or "").strip()[:80]
    if len(name) < 2:
        raise HTTPException(422, "Type the name of the item")
    if body.price is None or body.price <= 0:
        raise HTTPException(422, "Type the price per unit (what the customer paid for one)")
    if body.qty is None or body.qty <= 0:
        raise HTTPException(422, "Type how many the customer bought")
    if body.stock is not None and body.stock < 0:
        raise HTTPException(422, "The stock left cannot be below 0")
    return name, round(float(body.price), 2), float(body.qty), re.sub(r"\s+", " ", body.unit or "").strip()[:20] or None


@app.delete("/api/receipts/{rid}/unmatched/{uid}", tags=["review"])
def delete_unmatched(rid: int, uid: int, user=Depends(current_user), conn=Depends(get_db)):
    """'Not sold in this shop' -> the trash button: the line is wrong or not needed (also takes back a new-item
    request that the owner did not answer yet). Returns the whole receipt again."""
    r = own_receipt(conn, rid, user)
    own_unmatched(conn, rid, uid)
    with conn:
        conn.execute("DELETE FROM receipt_unmatched WHERE id=? AND receipt_id=?", (uid, rid))
    return detail(conn, r)


@app.post("/api/receipts/{rid}/unmatched/{uid}/request", tags=["review"])
def request_new_item(rid: int, uid: int, body: NewItemIn, user=Depends(current_user), conn=Depends(get_db)):
    """'Not sold in this shop' -> 'New item': the sales person writes the name, the price, how many the customer
    bought and (optional) how many are left in the shop. It waits under Supply (and on the dashboard) until the owner
    accepts it (it is then a normal row of this receipt: the sales person sells it with Update, Cash or Bank) or
    removes it."""
    r = own_receipt(conn, rid, user)
    own_unmatched(conn, rid, uid)
    name, price, qty, unit = new_item_values(body)
    with conn:
        conn.execute("""UPDATE receipt_unmatched SET req_name=?, req_price=?, req_qty=?, req_unit=?, req_stock=?, req_by=?, req_at=?
                        WHERE id=? AND receipt_id=?""", (name, price, qty, unit, body.stock, user["id"], now_iso(), uid, rid))
    E.log(f"[server] {user['username']} sent a new item for Supply: {name} x{qty:g} at {price:g} (receipt {rid})")
    return detail(conn, r)


@app.post("/api/receipts/{rid}/requests", tags=["review"])
def request_new_item_by_hand(rid: int, body: NewItemIn, user=Depends(current_user), conn=Depends(get_db)):
    """'Add a row' -> 'Not in the list? New item': an item the scan did not read at all, sent to the owner the same way."""
    r = own_receipt(conn, rid, user)
    if r["status"] != "done":
        raise HTTPException(409, "This photo is still being read")
    name, price, qty, unit = new_item_values(body)
    with conn:
        conn.execute("""INSERT INTO receipt_unmatched(receipt_id, ocr_text, item_text, qty, price, unit,
                                                      req_name, req_price, req_qty, req_unit, req_stock, req_by, req_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (rid, "(added by hand)", name, qty, price, unit, name, price, qty, unit, body.stock, user["id"], now_iso()))
    E.log(f"[server] {user['username']} sent a new item for Supply: {name} x{qty:g} at {price:g} (receipt {rid}, by hand)")
    return detail(conn, r)


class AlreadySold(Exception):
    pass


@app.post("/api/receipts/{rid}/commit", tags=["review"])
def commit(rid: int, body: Optional[CommitIn] = None, user=Depends(current_user), conn=Depends(get_db)):
    """The 'Update' button: deduct the stock and record the sale.
    If some items are short: 409 {"error":"not_enough_stock","short":[...]}; send again with {"skip_short":true}
    to sell only the items that are in stock (short rows stay on the receipt)."""
    skip_short = bool(body and body.skip_short)
    partial = bool(body and body.partial)
    r = own_receipt(conn, rid, user)
    if r["status"] != "done":
        raise HTTPException(409, "This photo is still being read")
    mode = ((body.pay_mode if body else None) or "").strip().lower()
    if mode:
        if mode not in ("cash", "bank"):
            raise HTTPException(422, "pay_mode is cash or bank")
        who = user["display_name"] or user["username"]
        with conn:                                  # how the customer paid: the sales person's answer on the phone
            conn.execute("UPDATE receipts SET pay_mode=?, pay_note=?, pay_sure=1 WHERE id=?",
                         (mode, f"chosen by {who} on the phone", rid))
        r = own_receipt(conn, rid, user)
    rows = conn.execute("SELECT * FROM receipt_lines WHERE receipt_id=? AND sold=0 ORDER BY id", (rid,)).fetchall()
    if not rows:
        raise HTTPException(409, "Nothing left to sell on this receipt")
    lines = [{"id": x["id"], "item_id": x["item_id"], "qty": x["qty"]} for x in rows]
    if any(l["qty"] is None or l["qty"] <= 0 for l in lines):
        raise HTTPException(422, "Some rows have no quantity. Edit them first.")

    items, need, short_ids, _ = E.stock_check(conn, lines)
    to_sell, left, reduced = lines, [], []
    if short_ids and partial:
        # give each short item's stock to its rows in order; rows that get nothing stay on the receipt
        to_sell, left = [], []
        avail = {i: items[i]["quantity"] for i in short_ids}
        for l in lines:
            if l["item_id"] not in short_ids:
                to_sell.append(l)
                continue
            give = min(l["qty"], max(0.0, avail[l["item_id"]]))
            if give <= 1e-9:
                left.append(l)
                continue
            avail[l["item_id"]] -= give
            if give < l["qty"] - 1e-9:
                reduced.append({"item": items[l["item_id"]]["name"], "unit": items[l["item_id"]]["unit"], "asked": l["qty"], "sold": give})
            to_sell.append({**l, "qty": give})
        if not to_sell:
            return JSONResponse(status_code=409, content={"error": "nothing_sellable", "short": [],
                                                          "message": "There is no stock at all for these items"})
        short_ids = set()
    if short_ids:
        short = [{"item_id": i, "item": items[i]["name"], "unit": items[i]["unit"], "need": need[i],
                  "have": items[i]["quantity"], "missing": need[i] - items[i]["quantity"]} for i in sorted(short_ids)]
        to_sell = [l for l in lines if l["item_id"] not in short_ids]
        left = [l for l in lines if l["item_id"] in short_ids]
        if not skip_short:
            return JSONResponse(status_code=409, content={
                "error": "not_enough_stock", "short": short,
                "message": "Not enough stock for: " + ", ".join(s["item"] for s in short)})
        if not to_sell:
            return JSONResponse(status_code=409, content={
                "error": "nothing_sellable", "short": short, "message": "Every item on this receipt is short on stock"})

    def mark_sold(cur, sale_id):                    # runs inside the SAME transaction as the stock deduction
        for l in to_sell:                           # a row sold partly keeps the amount actually sold
            cur.execute("UPDATE receipt_lines SET qty=?, sold=1, sale_id=? WHERE id=? AND sold=0", (l["qty"], sale_id, l["id"]))
            if cur.rowcount == 0:                   # Update pressed twice at once: the other press already sold it
                raise AlreadySold()
        # how it was paid: Cash or Bank as the sales person answered on the phone; the owner can switch it in Payments
        cur.execute("UPDATE sales SET pay_mode=?, pay_note=? WHERE id=?",
                    (r["pay_mode"] or "cash", r["pay_note"] or "not asked (an older phone app): counted as cash", sale_id))

    try:
        sale_id, total, _profit = E.commit_sale(conn, to_sell, r["image"], user_id=user["id"], on_sale=mark_sold)
    except AlreadySold:                             # nothing changed: the whole transaction was rolled back
        return JSONResponse(status_code=409, content={"error": "already_sold", "short": [],
                                                      "message": "This receipt was just updated (sold) already."})
    except ValueError as e:                         # stock vanished a moment ago (another sale)
        return JSONResponse(status_code=409, content={"error": "not_enough_stock", "message": str(e), "short": []})
    return {"sale_id": sale_id, "total": total, "sold": len(to_sell),
            "skipped": [items[l["item_id"]]["name"] for l in left], "reduced": reduced}


@app.post("/api/shop/orders/read-image", tags=["shop (owner)"])
def order_from_image(request: Request, image: UploadFile = File(...), user=Depends(current_user), conn=Depends(get_db)):
    """Sales -> Add Order -> 'Read from photo': the OCR reads the order (customer's list, a quotation, a receipt) and
    returns rows matched to OUR inventory (unit and price filled in from Supply). Lines we do not carry come back in
    `unmatched`. Nothing is saved: the owner checks and edits the rows, then presses Apply."""
    require_desk(request, user)
    data = image.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Image is larger than {MAX_UPLOAD_MB} MB")
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        im.thumbnail((2400, 2400))
    except Exception:
        raise HTTPException(422, "That file is not a readable image")
    path = UPLOAD_DIR / f"order-read-{secrets.token_hex(6)}.jpg"
    im.save(path, "JPEG", quality=90)
    try:
        d = SERVICE.read_now(path)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    finally:
        path.unlink(missing_ok=True)
    lines, unmatched = E.build_lines_full(d["boxes"], conn, loose=True)    # handwritten lists: article table matched loosely
    items = {i["id"]: i for i in E.get_items(conn)}
    texts = E.group_rows(d["boxes"])
    found = shop_api.read_delivery(texts) or {}
    return {"customer": shop_api.text_customer(texts), "rows": shop_api.order_rows(lines, items), "unmatched": unmatched,
            "address": found.get("address", ""), "date": found.get("date"), "seconds": d.get("seconds")}


# =====================================================================
# THE DESKTOP APP ITSELF (built React files), so the .exe needs no Node/Vite: the window opens http://127.0.0.1:8000/
# =====================================================================
if WEB_DIR:
    from fastapi.staticfiles import StaticFiles
    if (WEB_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def desktop_page(path: str, request: Request):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        f = (WEB_DIR / path).resolve()
        if path and f.is_file() and WEB_DIR.resolve() in f.parents:      # favicon, images from public/
            return FileResponse(f)
        if not is_local(request):
            raise HTTPException(404, "The desktop pages only open on the shop laptop itself.")
        return FileResponse(WEB_DIR / "index.html")                       # /sales, /supply ... (React routes)


# =====================================================================
# COMMAND LINE: create users, start the server
# =====================================================================
def lan_addresses():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))            # sends nothing; just asks the OS which address it would use
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith(("127.", "169.254.", "192.168.56.")))



def cmd_add_user(a):
    init_server_db()
    pw = getpass.getpass("Password for the new account: ")
    if len(pw) < 4 or pw != getpass.getpass("Type it again: "):
        sys.exit("Passwords did not match (or were shorter than 4 characters). Nothing was created.")
    conn = db()
    try:
        avatar = save_avatar(a.avatar, a.username) if a.avatar else None
        with conn:
            conn.execute("INSERT INTO users(username, display_name, password_hash, avatar, is_admin, created) VALUES(?,?,?,?,?,?)",
                         (a.username, a.name or a.username, hash_password(pw), avatar, 1 if a.admin else 0, now_iso()))
        print(f"Created {'OWNER ' if a.admin else ''}account '{a.username}'.")
    except sqlite3.IntegrityError:
        sys.exit(f"The username '{a.username}' already exists.")
    finally:
        conn.close()


def cmd_make_admin(a, admin):
    init_server_db()
    conn = db()
    with conn:
        n = conn.execute("UPDATE users SET is_admin=? WHERE username=?", (1 if admin else 0, a.username)).rowcount
    conn.close()
    print(("Owner access granted." if admin else "Owner access removed.") if n else "No such user.")


def cmd_users(a):
    init_server_db()
    conn = db()
    for u in conn.execute("SELECT * FROM users ORDER BY id"):
        print(f"{u['id']:>3}  {u['username']:<16} {u['display_name'] or '':<22} "
              f"{'active' if u['active'] else 'DISABLED':<9} avatar: {u['avatar'] or '-'}")
    conn.close()


def cmd_passwd(a):
    init_server_db()
    pw = getpass.getpass("New password: ")
    conn = db()
    with conn:
        n = conn.execute("UPDATE users SET password_hash=? WHERE username=?", (hash_password(pw), a.username)).rowcount
        if n:
            conn.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)", (a.username,))
    conn.close()
    print("Password changed (the user was signed out)." if n else "No such user.")


def cmd_avatar(a):
    init_server_db()
    conn = db()
    with conn:
        n = conn.execute("UPDATE users SET avatar=? WHERE username=?", (save_avatar(a.image, a.username), a.username)).rowcount
    conn.close()
    print("Profile picture updated." if n else "No such user.")


def cmd_enable(a, active):
    init_server_db()
    conn = db()
    with conn:
        n = conn.execute("UPDATE users SET active=? WHERE username=?", (1 if active else 0, a.username)).rowcount
        if n and not active:
            conn.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)", (a.username,))
    conn.close()
    print(("Enabled." if active else "Disabled (and signed out).") if n else "No such user.")


def cmd_system_password(a):
    """Forgot it? Set a new system password from the laptop itself."""
    init_server_db()
    pw = getpass.getpass("New system password: ")
    if len(pw) < 4 or pw != getpass.getpass("Type it again: "):
        sys.exit("Passwords did not match (or were shorter than 4 characters). Nothing changed.")
    conn = db()
    with conn:
        shop_db.set_setting(conn, "system_password", hash_password(pw))
        shop_db.audit(conn, None, "System password changed", "from the command line")
    conn.close()
    print("System password changed.")


def cmd_seed_demo(a):
    init_server_db()
    conn = db()
    n = E.seed_demo(conn)
    conn.close()
    print(f"Added {n} sample item(s) (nails / screws). Delete them later in Supply if you do not need them.")


def cmd_cleanup(a):
    """Free disk space: delete the PHOTO FILES of finished receipts older than --days (done, nothing left unsold).
    The receipts, rows and sales stay in the database; only the picture disappears."""
    init_server_db()
    conn = db()
    cutoff = (datetime.now() - timedelta(days=a.days)).isoformat(timespec="seconds")
    rows = conn.execute("""SELECT r.id, r.image FROM receipts r WHERE r.created < ? AND r.status = 'done'
                           AND NOT EXISTS (SELECT 1 FROM receipt_lines l WHERE l.receipt_id = r.id AND l.sold = 0)""",
                        (cutoff,)).fetchall()
    n = freed = 0
    for r in rows:
        files = ([Path(r["image"])] if r["image"] else []) + list(THUMB_DIR.glob(f"{r['id']}_*.jpg"))
        for p in files:
            try:
                if p.is_file():
                    size = p.stat().st_size
                    if not a.dry_run:
                        p.unlink()
                    freed += size
                    n += 1
            except OSError:
                pass
    conn.close()
    print(f"{'Would delete' if a.dry_run else 'Deleted'} {n} file(s), {freed / 1048576:.1f} MB "
          f"(receipts finished more than {a.days} days ago). The sales records are kept.")


def cmd_serve(a):
    import uvicorn
    global SERVE_PORT, ALLOW_REMOTE_DESK
    SERVE_PORT = a.port
    ALLOW_REMOTE_DESK = ALLOW_REMOTE_DESK or getattr(a, "allow_remote_desk", False)
    SERVICE.configure(a.device, a.tier, a.side)
    print("\n" + "=" * 64)
    print(" Shop OCR server starting.  Type this address into the phone app:")
    for ip in lan_addresses() or ["<your-laptop-ip>"]:
        print(f"      http://{ip}:{a.port}")
    print(f" API documentation (open in a browser):  http://localhost:{a.port}/docs")
    print("=" * 64 + "\n")
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Shop OCR server (runs on the laptop)")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="start the server (default)")
    s.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from the phone")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--device", choices=["auto", "cpu", "gpu"], default="auto")
    s.add_argument("--tier", choices=list(E.MODEL_SETS), default="medium")
    s.add_argument("--side", type=int, default=E.DEFAULT_SIDE)
    s.add_argument("--allow-remote-desk", action="store_true",
                   help="also let OTHER PCs on the Wi-Fi open the desktop pages (default: only this laptop)")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("add-user", help="create a phone account")
    s.add_argument("username")
    s.add_argument("--name", help="name shown in the app")
    s.add_argument("--avatar", help="path to a profile picture")
    s.add_argument("--admin", action="store_true", help="owner account (can open the React desktop app)")
    s.set_defaults(fn=cmd_add_user)
    sub.add_parser("set-system-password", help="set a new system password (if it was forgotten)").set_defaults(fn=cmd_system_password)
    sub.add_parser("seed-demo", help="add 7 sample items (nails/screws) for testing the OCR").set_defaults(fn=cmd_seed_demo)

    sub.add_parser("list-users", help="show all accounts").set_defaults(fn=cmd_users)
    for name, flag in (("make-admin", True), ("remove-admin", False)):
        s = sub.add_parser(name, help="give / take away owner access (needed for the React desktop app)")
        s.add_argument("username")
        s.set_defaults(fn=lambda a, f=flag: cmd_make_admin(a, f))
    s = sub.add_parser("cleanup-images", help="delete old photo files of finished receipts to free disk space")
    s.add_argument("--days", type=int, default=30, help="only receipts older than this (default 30)")
    s.add_argument("--dry-run", action="store_true", help="only show what would be deleted")
    s.set_defaults(fn=cmd_cleanup)
    s = sub.add_parser("passwd", help="change a password")
    s.add_argument("username")
    s.set_defaults(fn=cmd_passwd)
    s = sub.add_parser("set-avatar", help="change a profile picture")
    s.add_argument("username")
    s.add_argument("image")
    s.set_defaults(fn=lambda a: cmd_avatar(argparse.Namespace(username=a.username, image=a.image)))
    for name, flag in (("disable-user", False), ("enable-user", True)):
        s = sub.add_parser(name, help=f"{name.split('-')[0]} an account")
        s.add_argument("username")
        s.set_defaults(fn=lambda a, f=flag: cmd_enable(a, f))

    a = ap.parse_args(argv)
    if not a.cmd:                                    # plain `python server.py` = serve
        a = ap.parse_args(["serve"])
    a.fn(a)


if __name__ == "__main__":
    main()