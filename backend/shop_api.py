"""
shop_api.py  =  owner-only routes under /api/shop/* used by the React desktop app.

It is plugged into server.py with   app.include_router(shop_api.build_router(current_user, get_db, AVATAR_DIR, UPLOAD_DIR, THUMB_DIR, save_avatar, hash_password))
so there is no circular import. Every route needs   Authorization: Bearer <token>   of an account with users.is_admin = 1
(make one with:  python server.py make-admin <username>).  <img> tags can use  ?token=<token>  instead of the header.

React entries use ids like "emp-3", "loan-2", "pay-5", "mnt-4", "cus-7" because several tables feed one list.
"""
import csv
import io
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, Header
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
from PIL import Image, ImageOps

import shop_db

DEFAULT_POSITIONS = ["Labor", "Sales Person", "Driver", "Backhoe Operator"]
PREFIX = {"employee": "emp", "loan": "loan", "payment": "pay", "maintenance": "mnt", "custom": "cus"}
FROM_PREFIX = {v: k for k, v in PREFIX.items()}

EMP_MAP = {"name": "name", "position": "position", "ratePerHour": "rate_per_hour", "perDayRate": "per_day_rate",
           "presentDays": "present_days", "absentDays": "absent_days", "overtimeHours": "overtime_hours",
           "undertimeHours": "undertime_hours", "notes": "notes"}
OBL_MAP = {"name": "name", "lender": "party", "payee": "party", "startDate": "start_date", "endDate": "end_date",
           "freqValue": "freq_value", "freqUnit": "freq_unit", "principal": "principal", "interestRate": "interest_rate",
           "finalPayment": "final_payment", "status": "status"}
MNT_MAP = {"name": "name", "payment": "amount", "freqValue": "freq_value", "freqUnit": "freq_unit",
           "startDate": "start_date", "lastPaid": "last_paid", "notes": "notes"}
CUS_MAP = {"name": "name", "amount": "amount", "basis": "basis", "notes": "notes"}
TABLE = {"employee": ("employees", EMP_MAP), "loan": ("obligations", OBL_MAP), "payment": ("obligations", OBL_MAP),
         "maintenance": ("maintenance_items", MNT_MAP), "custom": ("custom_items", CUS_MAP)}
LOG_TABLE = {  # React field -> (table, parent column, extra column values)
    "cashAdvances": ("employee_cash_advances", "employee_id", {}),
    "customPayments": ("employee_payments", "employee_id", {"kind": "bonus"}),
}


def now():
    return datetime.now().isoformat(timespec="seconds")


def peso_txt(n):
    return f"₱{(n or 0):,.2f}".replace(".00", "")


def fields_set(model):
    """Which fields the client actually sent (so 'levelA: null' can clear a level)."""
    return getattr(model, "model_fields_set", None) or getattr(model, "__fields_set__", set())


def today():
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Reading an ORDER out of OCR text rows (Sales -> Add Order -> Read from photo, and delivery receipts sent from the
# phones). Module level so server.py's OCR thread can use it too.
# ---------------------------------------------------------------------------
import json as _json_mod
import re as _re_mod

CUSTOMER_RE = _re_mod.compile(r"^\s*(?:to|sold\s*to|bill\s*to|ship\s*to|deliver(?:ed)?\s*to|customer|client|name|charge\s*to)"
                              r"\s*[:.\-]?\s+(.+)$", _re_mod.I)
ADDRESS_RE = _re_mod.compile(r"^\s*(?:address|addr|delivery\s*address|location|site|brgy|barangay)"
                             r"\s*[:.\-]?\s+(.+)$", _re_mod.I)
# words that make a receipt a DELIVERY: "Delivery Receipt", "Deliver to", "For delivery", "D.R. No. 123", "Ship to"
DELIVERY_RE = _re_mod.compile(r"\bdeliver(?:y|ies|ed)?\b|\bd\.?\s?r\.?\s*(?:no\.?|#)|\bship\s*to\b|\bdrop[\s-]?off\b", _re_mod.I)
DATE_RES = [(_re_mod.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})"), "mdy"),
            (_re_mod.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s*(\d{4})?", _re_mod.I), "mon")]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def text_customer(texts):
    for text in texts:
        m = CUSTOMER_RE.match(text)
        if m:
            return _re_mod.split(r"\s{2,}|\bcontact\b|\bdate\b|\btel\b|\baddress\b|\bterms?\s*:|\btin\s*:",
                                 m.group(1), flags=_re_mod.I)[0].strip(" :-,")
    return ""


# printed on every receipt booklet by the printer (BIR): never the delivery date / the customer's address
PRINTER_RE = _re_mod.compile(r"date\s*issued|accreditation|authority\s*to\s*print|\bprinter|\bbir\b|business\s*forms", _re_mod.I)
# a table header that sits right after "Address:" on many receipts ("Address:  ARTICLES") is not an address
HEADER_WORDS_RE = _re_mod.compile(r"^(?:articles?|qty\.?|quantity|unit|description|particulars|amount|price)\b", _re_mod.I)


def text_date(text):
    """a date written on the receipt (10/25/2026, Oct 25 2026, Oct 25) -> YYYY-MM-DD, or None"""
    for rx, kind in DATE_RES:
        m = rx.search(text)
        if not m:
            continue
        try:
            if kind == "mdy":
                mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                y = y + 2000 if y < 100 else y
            else:
                mo, d = _MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2))
                y = int(m.group(3)) if m.group(3) else datetime.now().year
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            continue
    return None


def read_delivery(texts):
    """None when the receipt is not a delivery; else {words, customer, address, date} read from the text rows"""
    words = []
    for t in texts:
        for m in DELIVERY_RE.finditer(t):
            w = m.group(0).strip()
            if w.lower() not in [x.lower() for x in words]:
                words.append(w)
    if not words:
        return None
    address, date = "", None
    for t in texts:
        m = ADDRESS_RE.match(t)
        if m and not address and not HEADER_WORDS_RE.match(m.group(1).strip()):
            address = m.group(1).strip(" :-,")
        if date is None and not PRINTER_RE.search(t) and _re_mod.search(r"deliver|sched|date|petsa", t, _re_mod.I):
            date = text_date(t)
    return {"words": words, "customer": text_customer(texts), "address": address, "date": date}


def order_rows(lines, items_by_id):
    return [{"itemId": l["item_id"], "name": items_by_id[l["item_id"]]["name"], "unit": items_by_id[l["item_id"]]["unit"],
             "price": items_by_id[l["item_id"]]["sell_price"], "qty": l["qty"], "score": round(l["score"]), "ocr": l["ocr"]}
            for l in lines if l["item_id"] in items_by_id]


def save_delivery_draft(conn, receipt_id, user_id, texts, lines, unmatched, items_by_id):
    """Called by the OCR thread when a phone's receipt is read. A delivery receipt becomes (or refreshes) a draft order
    for the owner. Returns the draft id, or None when the receipt is not a delivery. Never raises."""
    try:
        found = read_delivery(texts)
        if not found:
            return None
        data = {"rows": order_rows(lines, items_by_id), "unmatched": [{"item": u["item"], "qty": u["qty"]} for u in unmatched],
                "words": found["words"], "text": texts[:60]}
        cur = conn.execute("SELECT id, status FROM delivery_drafts WHERE receipt_id=?", (receipt_id,)).fetchone()
        if cur:
            if cur[1] != "new":                                  # already applied / dismissed: leave it alone
                return cur[0]
            conn.execute("UPDATE delivery_drafts SET customer=?, address=?, delivery_date=?, data=? WHERE id=?",
                         (found["customer"], found["address"], found["date"], _json_mod.dumps(data), cur[0]))
            return cur[0]
        return conn.execute("""INSERT INTO delivery_drafts(receipt_id, user_id, created, status, customer, address, delivery_date, data)
                               VALUES(?,?,?,'new',?,?,?,?)""",
                            (receipt_id, user_id, now(), found["customer"], found["address"], found["date"],
                             _json_mod.dumps(data))).lastrowid
    except Exception:                                           # a draft must never break reading the receipt
        return None


class EntryIn(BaseModel):
    class Config:
        extra = "allow"                      # the React entry object, any field


class LogIn(BaseModel):
    amount: float
    note: Optional[str] = ""
    date: Optional[str] = None


class LabelIn(BaseModel):
    label: str


class AccountIn(BaseModel):
    username: str
    password: str


def build_router(current_user, get_db, AVATAR_DIR, UPLOAD_DIR, THUMB_DIR, save_avatar, hash_password,
                 require_desk=None, reauth=None, reread=None, drop_receipt=None):
    r = APIRouter(prefix="/api/shop", tags=["shop (owner)"])
    BASE = AVATAR_DIR.parent
    PRODUCT_DIR = BASE / "product_images"                 # supply photos: <product id>.jpg
    PAY_DIR = BASE / "payment_photos"                     # Payments: receipt photos <entry id>.jpg

    def check(request, user):
        if require_desk:                                  # server.py: desktop account + system password + this laptop
            require_desk(request, user)
        elif not user["is_admin"]:
            raise HTTPException(403, "Desktop account required (this is a phone login)")

    # ---------------- auth ----------------
    def admin(request: Request, user=Depends(current_user)):
        check(request, user)
        return user

    def admin_q(request: Request, token: Optional[str] = Query(None), authorization: Optional[str] = Header(None),
                conn=Depends(get_db)):
        """Same as admin, but also accepts ?token= so <img src> works without a header."""
        header = authorization or (f"Bearer {token}" if token else None)
        user = current_user(authorization=header, conn=conn)
        check(request, user)
        return user

    def note(conn, user, action, detail=""):
        """One line in the activity log (Calendar page: what the person in charge did)."""
        with conn:
            shop_db.audit(conn, user["id"], action, detail)

    def iname(conn, iid):
        x = conn.execute("SELECT name, unit FROM items WHERE id=?", (iid,)).fetchone()
        return (x["name"], x["unit"]) if x else (f"item {iid}", "")

    def parse_id(eid):
        try:
            p, n = eid.split("-")
            return FROM_PREFIX[p], int(n)
        except Exception:
            raise HTTPException(404, "Unknown entry id")

    def clean(v):
        return None if v == "" else v

    # ---------------- serialisers (DB row -> the object cashflow.jsx already uses) ----------------
    def logs(conn, table, col, pid, where="", args=()):
        return [{"id": f"{table[:1]}{x['id']}", "date": x["date"], "amount": x["amount"], "note": x["note"] or ""}
                for x in conn.execute(f"SELECT * FROM {table} WHERE {col}=? {where} ORDER BY id", (pid, *args))]

    def emp_entry(conn, e):
        return {"id": f"emp-{e['id']}", "type": "employee", "name": e["name"], "position": e["position"] or "",
                "ratePerHour": e["rate_per_hour"], "perDayRate": e["per_day_rate"], "presentDays": e["present_days"],
                "absentDays": e["absent_days"], "overtimeHours": e["overtime_hours"], "notes": e["notes"] or "",
                "undertimeHours": e["undertime_hours"], "temporary": bool(e["temporary"]),
                "hasPhoto": bool(e["photo"]), "userId": e["user_id"],
                "username": (conn.execute("SELECT username FROM users WHERE id=?", (e["user_id"],)).fetchone() or [""])[0]
                if e["user_id"] else "",
                "cashAdvances": logs(conn, "employee_cash_advances", "employee_id", e["id"]),
                "payments": logs(conn, "employee_payments", "employee_id", e["id"], "AND kind=?", ("payroll",)),
                "customPayments": logs(conn, "employee_payments", "employee_id", e["id"], "AND kind=?", ("bonus",))}

    def obl_entry(conn, o):
        d = {"id": f"{PREFIX[o['kind']]}-{o['id']}", "type": o["kind"], "name": o["name"],
             "startDate": o["start_date"] or "", "endDate": o["end_date"] or "", "freqValue": o["freq_value"],
             "freqUnit": o["freq_unit"], "finalPayment": o["final_payment"], "status": o["status"],
             "payments": logs(conn, "obligation_payments", "obligation_id", o["id"])}
        if o["kind"] == "loan":
            d.update({"lender": o["party"] or "", "principal": o["principal"], "interestRate": o["interest_rate"]})
        else:
            d["payee"] = o["party"] or ""
        return d

    def mnt_entry(m):
        return {"id": f"mnt-{m['id']}", "type": "maintenance", "name": m["name"], "payment": m["amount"],
                "freqValue": m["freq_value"], "freqUnit": m["freq_unit"], "startDate": m["start_date"] or "",
                "lastPaid": m["last_paid"] or "", "notes": m["notes"] or ""}

    def cus_entry(c):
        return {"id": f"cus-{c['id']}", "type": "custom", "customKey": f"cu{c['custom_type_id']}", "name": c["name"],
                "amount": c["amount"], "basis": c["basis"], "freqValue": 1, "freqUnit": "months", "notes": c["notes"] or ""}

    def get_entry(conn, eid):
        kind, n = parse_id(eid)
        if kind == "employee":
            row = conn.execute("SELECT * FROM employees WHERE id=?", (n,)).fetchone()
            ser = lambda x: emp_entry(conn, x)
        elif kind in ("loan", "payment"):
            row = conn.execute("SELECT * FROM obligations WHERE id=? AND kind=?", (n, kind)).fetchone()
            ser = lambda x: obl_entry(conn, x)
        elif kind == "maintenance":
            row = conn.execute("SELECT * FROM maintenance_items WHERE id=?", (n,)).fetchone()
            ser = mnt_entry
        else:
            row = conn.execute("SELECT * FROM custom_items WHERE id=?", (n,)).fetchone()
            ser = cus_entry
        if not row:
            raise HTTPException(404, "Not found")
        return kind, n, row, ser(row)

    def ensure_positions(conn):
        if conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0:
            with conn:
                conn.executemany("INSERT INTO positions(name) VALUES(?)", [(p,) for p in DEFAULT_POSITIONS])

    # ---------------- cashflow ----------------
    @r.get("/cashflow")
    def cashflow(user=Depends(admin), conn=Depends(get_db)):
        ensure_positions(conn)
        entries = [emp_entry(conn, e) for e in conn.execute("SELECT * FROM employees WHERE active=1 ORDER BY id")]
        entries += [obl_entry(conn, o) for o in conn.execute("SELECT * FROM obligations ORDER BY id")]
        entries += [mnt_entry(m) for m in conn.execute("SELECT * FROM maintenance_items ORDER BY id")]
        entries += [cus_entry(c) for c in conn.execute("SELECT * FROM custom_items ORDER BY id")]
        return {"entries": entries,
                "customTypes": [{"key": f"cu{t['id']}", "label": t["label"]} for t in conn.execute("SELECT * FROM custom_types ORDER BY id")],
                "positions": [p["name"] for p in conn.execute("SELECT name FROM positions ORDER BY id")]}

    def write_fields(conn, kind, n, body, creating=False):
        table, mp = TABLE[kind]
        cols = {mp[k]: clean(v) for k, v in body.items() if k in mp}
        if kind == "employee" and "userId" in body:
            cols["user_id"] = body["userId"]
        if kind == "employee" and cols.get("position"):
            conn.execute("INSERT OR IGNORE INTO positions(name) VALUES(?)", (cols["position"],))
        if not creating:
            if not cols:
                return
            sets = ", ".join(f"{c}=?" for c in cols)
            conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*cols.values(), n))
        return cols

    @r.post("/entries")
    def create_entry(body: EntryIn, user=Depends(admin), conn=Depends(get_db)):
        b = body.dict()
        kind = b.get("type")
        if kind not in TABLE:
            raise HTTPException(422, "type must be employee, loan, payment, maintenance or custom")
        table, mp = TABLE[kind]
        cols = write_fields(conn, kind, None, b, creating=True)
        if not (cols.get("name") or "").strip():
            raise HTTPException(422, "name is required")
        if kind in ("loan", "payment"):
            cols["kind"] = kind
        if kind == "employee":
            cols["created"] = now()
        if kind == "custom":
            try:
                cols["custom_type_id"] = int(str(b.get("customKey", ""))[2:])
            except ValueError:
                raise HTTPException(422, "customKey is required")
        try:
            with conn:
                cur = conn.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values()))
                n = cur.lastrowid
                if kind == "employee":
                    for adv in b.get("cashAdvances") or []:
                        conn.execute("INSERT INTO employee_cash_advances(employee_id,amount,note,date) VALUES(?,?,?,?)",
                                     (n, adv["amount"], adv.get("note"), adv.get("date") or today()))
        except sqlite3.IntegrityError as e:
            raise HTTPException(409, str(e))
        note(conn, user, "Added", f"{kind}: {cols.get('name')}")
        return get_entry(conn, f"{PREFIX[kind]}-{n}")[3]

    @r.put("/entries/{eid}")
    def update_entry(eid: str, body: EntryIn, user=Depends(admin), conn=Depends(get_db)):
        kind, n, row, _ = get_entry(conn, eid)
        b = body.dict()
        try:
            with conn:
                write_fields(conn, kind, n, b)
                if kind == "maintenance" and b.get("lastPaid") and b["lastPaid"] != row["last_paid"]:
                    conn.execute("INSERT INTO maintenance_payments(item_id,amount,date) VALUES(?,?,?)",
                                 (n, row["amount"] if "payment" not in b else b["payment"], b["lastPaid"]))
                if kind == "employee" and "userId" in b:
                    sync_user_avatar(conn, n)
        except sqlite3.IntegrityError as e:
            raise HTTPException(409, str(e))
        changed = ", ".join(f"{k} = {v}" for k, v in b.items() if k not in ("id", "type", "payments", "cashAdvances", "customPayments"))
        if changed:
            note(conn, user, "Edited", f"{row['name']}: {changed}"[:300])
        return get_entry(conn, eid)[3]

    @r.delete("/entries/{eid}")
    def delete_entry(eid: str, user=Depends(admin), conn=Depends(get_db)):
        kind, n, row, _ = get_entry(conn, eid)
        table = TABLE[kind][0]
        with conn:
            if kind == "employee":
                conn.execute("DELETE FROM employee_cash_advances WHERE employee_id=?", (n,))
                conn.execute("DELETE FROM employee_payments WHERE employee_id=?", (n,))
                conn.execute("DELETE FROM attendance WHERE employee_id=?", (n,))
                conn.execute("DELETE FROM work_hours WHERE employee_id=?", (n,))
                conn.execute("DELETE FROM run_crew WHERE employee_id=?", (n,))
                conn.execute("UPDATE bulk_order_runs SET driver_id=NULL WHERE driver_id=?", (n,))
                uid = conn.execute("SELECT user_id FROM employees WHERE id=?", (n,)).fetchone()[0]
                if uid:                                    # their phone login stops working; past sales stay
                    conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
                    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            if kind in ("loan", "payment"):
                conn.execute("DELETE FROM obligation_payments WHERE obligation_id=?", (n,))
            if kind == "maintenance":
                conn.execute("DELETE FROM maintenance_payments WHERE item_id=?", (n,))
            conn.execute(f"DELETE FROM {table} WHERE id=?", (n,))
        note(conn, user, "Deleted", f"{kind}: {row['name']}")
        return {"deleted": eid}

    @r.post("/entries/{eid}/log/{field}")
    def add_log(eid: str, field: str, body: LogIn, user=Depends(admin), conn=Depends(get_db)):
        """field = cashAdvances | customPayments | payments  (payments = payroll for employees, installments for loan/payment)."""
        kind, n, _, _ = get_entry(conn, eid)
        if body.amount == 0:
            raise HTTPException(422, "amount must not be 0")
        date = body.date or today()
        with conn:
            if field in LOG_TABLE and kind == "employee":
                table, col, extra = LOG_TABLE[field]
                if table == "employee_payments":
                    conn.execute("INSERT INTO employee_payments(employee_id,kind,amount,note,date) VALUES(?,?,?,?,?)",
                                 (n, extra["kind"], body.amount, body.note, date))
                else:
                    conn.execute("INSERT INTO employee_cash_advances(employee_id,amount,note,date) VALUES(?,?,?,?)",
                                 (n, body.amount, body.note, date))
            elif field == "payments" and kind == "employee":
                conn.execute("INSERT INTO employee_payments(employee_id,kind,amount,note,date) VALUES(?,'payroll',?,?,?)",
                             (n, body.amount, body.note, date))
            elif field == "payments" and kind in ("loan", "payment"):
                conn.execute("INSERT INTO obligation_payments(obligation_id,amount,note,date) VALUES(?,?,?,?)",
                             (n, body.amount, body.note, date))
            else:
                raise HTTPException(422, "This entry has no such log")
        what = {"cashAdvances": "Cash advance", "customPayments": "Bonus", "payments": "Payment"}[field]
        note(conn, user, what, f"{peso_txt(body.amount)} - {get_entry(conn, eid)[3]['name']}" + (f" ({body.note})" if body.note else ""))
        return get_entry(conn, eid)[3]

    @r.post("/custom-types")
    def add_custom_type(body: LabelIn, user=Depends(admin), conn=Depends(get_db)):
        label = body.label.strip()
        if not label:
            raise HTTPException(422, "label is required")
        try:
            with conn:
                cur = conn.execute("INSERT INTO custom_types(label) VALUES(?)", (label,))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That category already exists")
        note(conn, user, "New category", label)
        return {"key": f"cu{cur.lastrowid}", "label": label}

    @r.delete("/custom-types/{key}")
    def delete_custom_type(key: str, user=Depends(admin), conn=Depends(get_db)):
        try:
            tid = int(key[2:])
        except ValueError:
            raise HTTPException(404, "Unknown category")
        with conn:
            conn.execute("DELETE FROM custom_items WHERE custom_type_id=?", (tid,))
            conn.execute("DELETE FROM custom_types WHERE id=?", (tid,))
        return {"deleted": key}

    @r.post("/positions")
    def add_position(body: LabelIn, user=Depends(admin), conn=Depends(get_db)):
        with conn:
            conn.execute("INSERT OR IGNORE INTO positions(name) VALUES(?)", (body.label.strip(),))
        return {"ok": True}

    # ---------------- employee photo + phone account ----------------
    def sync_user_avatar(conn, emp_id):
        """If the employee has a photo AND a linked phone account, the phone loads that photo at login."""
        e = conn.execute("SELECT e.photo, u.username FROM employees e LEFT JOIN users u ON u.id=e.user_id WHERE e.id=?",
                         (emp_id,)).fetchone()
        if e and e["photo"] and e["username"] and (AVATAR_DIR / e["photo"]).exists():
            name = f"{e['username'].lower()}.jpg"
            (AVATAR_DIR / name).write_bytes((AVATAR_DIR / e["photo"]).read_bytes())
            conn.execute("UPDATE users SET avatar=? WHERE username=?", (name, e["username"]))

    @r.post("/entries/{eid}/photo")
    def upload_photo(eid: str, image: UploadFile = File(...), user=Depends(admin), conn=Depends(get_db)):
        kind, n, _, _ = get_entry(conn, eid)
        if kind != "employee":
            raise HTTPException(422, "Only employees have a photo")
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(image.file.read()))).convert("RGB")
        except Exception:
            raise HTTPException(422, "That file is not an image")
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        name = f"emp{n}.jpg"
        ImageOps.fit(im, (256, 256)).save(AVATAR_DIR / name, "JPEG", quality=88)
        with conn:
            conn.execute("UPDATE employees SET photo=? WHERE id=?", (name, n))
            pass
        note(conn, user, "Photo", f"employee photo uploaded ({get_entry(conn, eid)[3]['name']})")
        with conn:
            sync_user_avatar(conn, n)
        return {"ok": True}

    class AccountSet(BaseModel):
        username: str
        password: Optional[str] = ""             # empty = keep the current password (when changing the username)

    @r.post("/entries/{eid}/account")
    @r.put("/entries/{eid}/account")
    def set_account(eid: str, body: AccountSet, user=Depends(admin), conn=Depends(get_db)):
        """The sales person's PHONE LOGIN (the only thing the phone app needs). Set by the owner in Cash Flow -> Employee.
        - employee already has a login  -> change its username and/or password (signs the phone out if the password changed)
        - username exists but belongs to no employee (e.g. an account made earlier) -> link it to this employee
        - otherwise -> create the login. The employee photo becomes the phone's profile picture."""
        kind, n, row, _ = get_entry(conn, eid)
        if kind != "employee":
            raise HTTPException(422, "Only employees can get a phone login")
        uname, pw = body.username.strip(), body.password or ""
        if not uname:
            raise HTTPException(422, "Type a username")
        if pw and len(pw) < 4:
            raise HTTPException(422, "The password needs at least 4 characters")
        other = conn.execute("SELECT * FROM users WHERE username=?", (uname,)).fetchone()
        try:
            with conn:
                if row["user_id"]:
                    if other and other["id"] != row["user_id"]:
                        raise HTTPException(409, "That username is already used by someone else")
                    old = conn.execute("SELECT username FROM users WHERE id=?", (row["user_id"],)).fetchone()[0]
                    conn.execute("UPDATE users SET username=?, display_name=?, active=1 WHERE id=?", (uname, row["name"], row["user_id"]))
                    if pw:
                        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), row["user_id"]))
                        conn.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
                    what = f"@{old} → @{uname}" if old.lower() != uname.lower() else f"@{uname}"
                    what += " (new password)" if pw else ""
                elif other:
                    if conn.execute("SELECT 1 FROM employees WHERE user_id=?", (other["id"],)).fetchone():
                        raise HTTPException(409, "That username already belongs to another employee")
                    conn.execute("UPDATE employees SET user_id=? WHERE id=?", (other["id"], n))
                    conn.execute("UPDATE users SET active=1 WHERE id=?", (other["id"],))
                    if pw:
                        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), other["id"]))
                    what = f"linked existing login @{uname}" + (" (new password)" if pw else "")
                else:
                    if len(pw) < 4:
                        raise HTTPException(422, "Type a password of at least 4 characters for the new login")
                    uid = conn.execute("INSERT INTO users(username,display_name,password_hash,created) VALUES(?,?,?,?)",
                                       (uname, row["name"], hash_password(pw), now())).lastrowid
                    conn.execute("UPDATE employees SET user_id=? WHERE id=?", (uid, n))
                    what = f"made @{uname}"
                sync_user_avatar(conn, n)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That username is taken")
        note(conn, user, "Phone login", f"{row['name']}: {what}")
        return get_entry(conn, eid)[3]

    @r.get("/people/{uid}/photo")
    def person_photo(uid: int, user=Depends(admin_q), conn=Depends(get_db)):
        row = conn.execute("""SELECT u.avatar AS a, COALESCE(e.photo,
                                     (SELECT photo FROM employees x WHERE lower(x.name)=lower(COALESCE(u.display_name,u.username))
                                      AND x.photo IS NOT NULL LIMIT 1)) AS p
                              FROM users u LEFT JOIN employees e ON e.user_id=u.id WHERE u.id=?""", (uid,)).fetchone()
        for name in (row["a"], row["p"]) if row else ():
            if name and (AVATAR_DIR / name).exists():
                return FileResponse(AVATAR_DIR / name, media_type="image/jpeg")
        raise HTTPException(404, "No photo")

    # ---------------- the signed-in desktop person (sidebar profile card) ----------------
    def me_dict(conn, uid):
        u = conn.execute("SELECT id, username, avatar FROM users WHERE id=?", (uid,)).fetchone()
        f = AVATAR_DIR / (u["avatar"] or "") if u["avatar"] else None
        has = bool(f and f.exists())
        a = conn.execute("SELECT first_seen FROM user_activity WHERE user_id=? AND date=?", (uid, datetime.now().strftime("%Y-%m-%d"))).fetchone()
        emp = conn.execute("""SELECT photo FROM employees WHERE photo IS NOT NULL AND (user_id=? OR lower(name)=lower(?))
                              LIMIT 1""", (uid, u["username"])).fetchone()
        any_photo = has or bool(emp and (AVATAR_DIR / emp["photo"]).exists())
        return {"id": u["id"], "username": u["username"], "hasPhoto": has, "anyPhoto": any_photo,
                "photoVer": int(f.stat().st_mtime) if has else 0,
                "since": a["first_seen"][11:16] if a else ""}

    @r.get("/me")
    def me_profile(user=Depends(admin), conn=Depends(get_db)):
        return me_dict(conn, user["id"])

    @r.post("/me/photo")
    def me_photo(image: UploadFile = File(...), user=Depends(admin), conn=Depends(get_db)):
        name = f"user{user['id']}.jpg"
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(image.file.read()))).convert("RGB")
        except Exception:
            raise HTTPException(422, "That file is not an image")
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        ImageOps.fit(im, (256, 256)).save(AVATAR_DIR / name, "JPEG", quality=88)
        with conn:
            conn.execute("UPDATE users SET avatar=? WHERE id=?", (name, user["id"]))
        note(conn, user, "Profile", "changed the profile picture")
        return me_dict(conn, user["id"])

    @r.delete("/me/photo")
    def me_photo_remove(user=Depends(admin), conn=Depends(get_db)):
        with conn:
            conn.execute("UPDATE users SET avatar=NULL WHERE id=?", (user["id"],))
        (AVATAR_DIR / f"user{user['id']}.jpg").unlink(missing_ok=True)
        return me_dict(conn, user["id"])

    @r.get("/employees/{n}/photo")
    def employee_photo(n: int, user=Depends(admin_q), conn=Depends(get_db)):
        row = conn.execute("SELECT photo FROM employees WHERE id=?", (n,)).fetchone()
        if not row or not row["photo"] or not (AVATAR_DIR / row["photo"]).exists():
            raise HTTPException(404, "No photo")
        return FileResponse(AVATAR_DIR / row["photo"], media_type="image/jpeg")

    # ---------------- sales monitor ----------------
    def state_of(status, n_lines, n_sold):
        if status != "done":
            return status
        if n_lines and n_sold == n_lines:
            return "sold"
        return "partial" if n_sold else "ready"

    def period_start(period):
        """day = today, week = this week (from Monday), month = this month, all = everything."""
        t0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "day":
            return t0.isoformat()
        if period == "week":
            return (t0 - timedelta(days=t0.weekday())).isoformat()
        if period == "month":
            return t0.replace(day=1).isoformat()
        return "0000"

    @r.get("/sales-people")
    def sales_people(period: str = "all", user=Depends(admin), conn=Depends(get_db)):
        start = period_start(period)
        rows = conn.execute("""
            SELECT u.id, u.username, u.display_name, u.avatar, e.id AS employee_id, e.position,
                   COALESCE(e.photo, (SELECT photo FROM employees x WHERE lower(x.name)=lower(COALESCE(u.display_name,u.username))
                                      AND x.photo IS NOT NULL LIMIT 1)) AS photo,
                   COALESCE((SELECT SUM(total) FROM sales s WHERE s.user_id=u.id AND s.created >= :st),0) AS total,
                   (SELECT COUNT(*) FROM sales s WHERE s.user_id=u.id AND s.created >= :st) AS n_sales,
                   (SELECT COUNT(*) FROM receipts rc WHERE rc.user_id=u.id AND rc.created >= :st) AS n_receipts,
                   (SELECT COUNT(*) FROM delivery_drafts dd WHERE dd.user_id=u.id AND dd.status='new') AS n_drafts
            FROM users u LEFT JOIN employees e ON e.user_id=u.id
            WHERE u.active=1 AND (u.is_admin=0 OR e.id IS NOT NULL OR EXISTS (SELECT 1 FROM receipts rc WHERE rc.user_id=u.id))
            ORDER BY u.id""", {"st": start}).fetchall()
        return [{"userId": x["id"], "username": x["username"], "name": x["display_name"] or x["username"],
                 "employeeId": x["employee_id"], "position": x["position"] or "", "counter": f"Counter {k + 1}",
                 "hasPhoto": bool(x["avatar"] or x["photo"]),
                 "total": x["total"], "nSales": x["n_sales"], "nReceipts": x["n_receipts"], "drafts": x["n_drafts"]}
                for k, x in enumerate(rows)]

    @r.get("/sales-summary")
    def sales_summary(period: str = "day", user=Depends(admin), conn=Depends(get_db)):
        """ALL sales of a period in one place: each sales person's receipts (phones), sales made on the desktop, and the
        orders added in Sales -> Add Order (their value; how much of it is paid). total = counters + desktop + orders."""
        start = period_start(period)
        people = [{"userId": x["uid"], "name": x["name"], "total": x["t"] or 0, "count": x["n"]} for x in conn.execute(
            """SELECT u.id uid, COALESCE(u.display_name, u.username) name, SUM(s.total) t, COUNT(*) n
               FROM sales s JOIN users u ON u.id=s.user_id WHERE s.created >= ? GROUP BY u.id ORDER BY t DESC""", (start,))]
        desk = conn.execute("SELECT COALESCE(SUM(total),0), COUNT(*) FROM sales WHERE user_id IS NULL AND created >= ?", (start,)).fetchone()
        orders = []
        for o in conn.execute("SELECT * FROM bulk_orders WHERE COALESCE(created,'') >= ? ORDER BY id DESC", (start,)):
            total = order_money(conn, o)["total"]          # items (+ the delivery when the customer pays it)
            paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM bulk_order_payments WHERE order_id=?", (o["id"],)).fetchone()[0]
            orders.append({"id": o["id"], "customer": o["customer"], "date": (o["created"] or "")[:10], "total": total, "paid": paid,
                           "status": o["status"]})
        counters = sum(p["total"] for p in people)
        orders_total = sum(o["total"] for o in orders)
        return {"period": period, "people": people, "counters": counters, "desktop": {"total": desk[0], "count": desk[1]},
                "orders": orders, "ordersTotal": orders_total, "ordersPaid": sum(o["paid"] for o in orders),
                "total": counters + desk[0] + orders_total}

    # (a desktop account shows as a counter too when it is linked to an employee or has sent receipts from a phone)
    @r.post("/sales-people/{uid}/remove")
    def remove_sales_person(uid: int, user=Depends(admin), conn=Depends(get_db)):
        """The small '-' on a counter card: switches the phone login off. Their receipts and sales stay in the books."""
        with conn:
            if conn.execute("SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone() == (1,):
                raise HTTPException(409, "This is a desktop account; it cannot be removed from here.")
            n = conn.execute("UPDATE users SET active=0 WHERE id=? AND is_admin=0", (uid,)).rowcount
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            conn.execute("UPDATE employees SET user_id=NULL WHERE user_id=?", (uid,))
        if not n:
            raise HTTPException(404, "Sales person not found")
        note(conn, user, "Removed counter", f"phone login #{uid} switched off")
        return {"removed": uid}

    @r.get("/sales-people/removed")
    def removed_sales_people(user=Depends(admin), conn=Depends(get_db)):
        """'Show hidden' on the Sales counters: phone logins that were switched off (their sales stay in the books)."""
        rows = conn.execute("""SELECT u.id, u.username, COALESCE(u.display_name, u.username) name,
                                      (SELECT COUNT(*) FROM sales s WHERE s.user_id=u.id) n,
                                      (SELECT COALESCE(SUM(total),0) FROM sales s WHERE s.user_id=u.id) t,
                                      (SELECT MAX(created) FROM sales s WHERE s.user_id=u.id) last_sale,
                                      (SELECT name FROM employees e WHERE e.user_id=u.id) emp
                               FROM users u WHERE u.active=0 AND u.is_admin=0 ORDER BY u.id""").fetchall()
        return [{"userId": x["id"], "username": x["username"], "name": x["name"], "nSales": x["n"], "total": x["t"],
                 "lastSale": x["last_sale"] or "", "employee": x["emp"] or ""} for x in rows]

    @r.post("/sales-people/{uid}/restore")
    def restore_sales_person(uid: int, user=Depends(admin), conn=Depends(get_db)):
        """Switch a phone login back on (same username and password as before)."""
        u = conn.execute("SELECT * FROM users WHERE id=? AND is_admin=0", (uid,)).fetchone()
        if not u:
            raise HTTPException(404, "Sales person not found")
        with conn:
            conn.execute("UPDATE users SET active=1 WHERE id=?", (uid,))
            if not conn.execute("SELECT 1 FROM employees WHERE user_id=?", (uid,)).fetchone():   # link the same-named employee again
                conn.execute("""UPDATE employees SET user_id=? WHERE id=(SELECT id FROM employees WHERE user_id IS NULL
                                AND lower(name)=lower(?) ORDER BY active DESC, id LIMIT 1)""", (uid, u["display_name"] or u["username"]))
        note(conn, user, "Counter back", f"phone login {u['username']} switched on")
        return {"restored": uid}

    @r.get("/sales-people/{uid}/transactions")
    def person_transactions(uid: int, limit: int = 300, user=Depends(admin), conn=Depends(get_db)):
        """Every item sold by this person (newest first), from sale_items = the price at the moment of the sale."""
        rows = conn.execute("""SELECT si.id, s.id AS sale_id, s.created, i.name, i.unit, si.qty, si.sell_price
                               FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN items i ON i.id=si.item_id
                               WHERE s.user_id=? ORDER BY s.id DESC, si.id LIMIT ?""", (uid, limit)).fetchall()
        return [{"id": x["id"], "saleId": x["sale_id"], "created": x["created"], "item": x["name"], "unit": x["unit"],
                 "qty": x["qty"], "amount": x["qty"] * x["sell_price"]} for x in rows]

    @r.get("/sales-people/{uid}/receipts")
    def person_receipts(uid: int, limit: int = 200, user=Depends(admin), conn=Depends(get_db)):
        out = []
        for rc in conn.execute("SELECT * FROM receipts WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)):
            lines = conn.execute("""SELECT l.qty, l.sold, l.sale_id, i.sell_price FROM receipt_lines l
                                    JOIN items i ON i.id=l.item_id WHERE l.receipt_id=?""", (rc["id"],)).fetchall()
            n_sold = sum(1 for x in lines if x["sold"])
            sale_ids = {x["sale_id"] for x in lines if x["sold"] and x["sale_id"]}
            sold_total = 0.0
            if sale_ids:
                q = ",".join("?" * len(sale_ids))
                sold_total = conn.execute(f"SELECT COALESCE(SUM(total),0) FROM sales WHERE id IN ({q})", tuple(sale_ids)).fetchone()[0]
            pending = sum((x["qty"] or 0) * x["sell_price"] for x in lines if not x["sold"])
            um = conn.execute("SELECT COUNT(*), COALESCE(SUM(COALESCE(price,0)*COALESCE(qty,1)),0) FROM receipt_unmatched WHERE receipt_id=?",
                              (rc["id"],)).fetchone()
            out.append({"id": rc["id"], "label": shop_db.receipt_label(rc["created"]), "created": rc["created"],
                        "state": state_of(rc["status"], len(lines), n_sold),
                        "total": sold_total if n_sold else pending, "sold": bool(n_sold),
                        "notInSupply": um[0], "notInSupplyTotal": um[1],      # rows the shop does not carry yet (Add to Supply)
                        "image_url": f"/api/shop/receipts/{rc['id']}/image"})
        return out

    def suggest_name(conn, text):
        """a readable name for what the receipt says ('TIVE AOHECLIVE' -> 'Tile Adhesive'), to start the Supply form"""
        if not (text or "").strip():
            return ""
        try:
            import ocr_engine as _E
            items = _E.get_items(conn)
            return _E.tidy_name(text, _E.rank_matches(text, items, loose=True), items)
        except Exception:
            return text.strip()

    @r.get("/receipts/{rid}")
    def receipt_detail(rid: int, user=Depends(admin), conn=Depends(get_db)):
        """One receipt for Sales -> Receipt: the rows matched to Supply items (not updated = the phone has not pressed
        Update yet) and the rows the shop does not carry (`unmatched`, each can be added to Supply in one click)."""
        rc = conn.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
        if not rc:
            raise HTTPException(404, "Receipt not found")
        lines = conn.execute("""SELECT l.id, l.ocr_text, l.qty, l.sold, l.score, l.verified, l.price, l.item_text, l.item_id,
                                       i.name AS item_name, i.unit, i.sell_price, i.qty_unknown
                                FROM receipt_lines l JOIN items i ON i.id=l.item_id WHERE l.receipt_id=? ORDER BY l.id""",
                             (rid,)).fetchall()
        unmatched = conn.execute("SELECT * FROM receipt_unmatched WHERE receipt_id=? ORDER BY id", (rid,)).fetchall()
        maybe = {}
        if unmatched:                              # "Is it ...?": an item already in Supply at exactly the receipt's price
            try:
                import ocr_engine as _E
                items_ = _E.get_items(conn)
                for u in unmatched:
                    it = _E.maybe_item(u["item_text"] or "", u["price"], u["unit"], items_)
                    if it is not None:
                        maybe[u["id"]] = {"itemId": it["id"], "name": it["name"], "unit": it["unit"], "sellPrice": it["sell_price"]}
            except Exception:
                maybe = {}
        return {"id": rid, "label": shop_db.receipt_label(rc["created"]), "created": rc["created"], "status": rc["status"],
                "payment": {"mode": rc["pay_mode"], "why": rc["pay_note"] or "", "sure": bool(rc["pay_sure"])},
                "lines": [{"id": x["id"], "itemId": x["item_id"], "item": x["item_name"], "unit": x["unit"], "qty": x["qty"],
                           "sold": bool(x["sold"]), "lineTotal": (x["qty"] or 0) * x["sell_price"], "sellPrice": x["sell_price"],
                           "price": x["price"], "ocr": x["ocr_text"] or "", "checked": bool(x["verified"]),
                           "sure": bool(x["verified"]) or (x["score"] or 0) >= 85, "qtyUnknown": bool(x["qty_unknown"]),
                           "suggest": "" if x["sold"] else suggest_name(conn, x["item_text"] or "")} for x in lines],
                "unmatched": [{"id": u["id"], "item": u["item_text"] or "", "qty": u["qty"], "price": u["price"],
                               "unit": u["unit"] or "", "ocr": u["ocr_text"] or "", "maybe": maybe.get(u["id"]),
                               "request": {"name": u["req_name"], "price": u["req_price"], "qty": u["req_qty"],
                                           "unit": u["req_unit"] or "", "at": u["req_at"],
                                           "by": (conn.execute("SELECT COALESCE(display_name, username) FROM users WHERE id=?",
                                                               (u["req_by"],)).fetchone() or [""])[0]}
                               if u["req_at"] else None}
                              for u in unmatched]}

    # ---- new items sent from the phones (round 17) ----
    # A sales person marks a "not sold in this shop" row as a NEW item: name, price, how many the customer bought
    # (receipt_unmatched.req_*) and how many are left. It waits here (Supply page, below the items; + a sticky dashboard
    # card) until the owner accepts it -> the item is added to Supply and the row goes back onto the receipt as a normal
    # row: the SALES PERSON sells it with Update on the phone (and says Cash or Bank) - or the owner removes it.
    def request_row(conn, uid):
        u = conn.execute("SELECT * FROM receipt_unmatched WHERE id=? AND req_at IS NOT NULL", (uid,)).fetchone()
        if not u:
            raise HTTPException(404, "That new item is not waiting any more (accepted, removed, or its receipt was deleted)")
        return u

    def request_dict(conn, u, items_=None):
        rc = conn.execute("SELECT * FROM receipts WHERE id=?", (u["receipt_id"],)).fetchone()
        who = conn.execute("SELECT COALESCE(display_name, username) FROM users WHERE id=?", (u["req_by"],)).fetchone()
        maybe = None
        try:
            import ocr_engine as _E
            it = _E.maybe_item(u["req_name"] or "", u["req_price"], u["req_unit"], items_ if items_ is not None else _E.get_items(conn))
            if it is not None:
                maybe = {"itemId": it["id"], "name": it["name"], "unit": it["unit"], "sellPrice": it["sell_price"]}
        except Exception:
            maybe = None
        same = conn.execute("SELECT id, name, unit, sell_price FROM items WHERE name=? COLLATE NOCASE AND deleted_at IS NULL",
                            (u["req_name"],)).fetchone()
        if same is not None:                       # exactly that name is already in Supply
            maybe = {"itemId": same["id"], "name": same["name"], "unit": same["unit"], "sellPrice": same["sell_price"]}
        return {"id": u["id"], "receiptId": u["receipt_id"],
                "receiptLabel": shop_db.receipt_label(rc["created"]) if rc and rc["created"] else "",
                "name": u["req_name"], "price": u["req_price"], "qty": u["req_qty"], "unit": u["req_unit"] or "",
                "amount": round((u["req_price"] or 0) * (u["req_qty"] or 0), 2), "read": u["item_text"] or "",
                "ocr": u["ocr_text"] or "", "byHand": (u["ocr_text"] or "") == "(added by hand)",
                "by": who[0] if who else "", "byId": u["req_by"], "at": u["req_at"],
                "stock": u["req_stock"],               # how many the sales person says are left (after this sale), or None
                "maybe": maybe}

    @r.get("/requests")
    def requests_list(user=Depends(admin), conn=Depends(get_db)):
        try:
            import ocr_engine as _E
            items_ = _E.get_items(conn)
        except Exception:
            items_ = None
        return [request_dict(conn, u, items_) for u in
                conn.execute("SELECT * FROM receipt_unmatched WHERE req_at IS NOT NULL ORDER BY req_at").fetchall()]

    class AcceptIn(BaseModel):
        name: str = ""
        unit: str = ""
        sellPrice: Optional[float] = None
        costPrice: Optional[float] = None        # empty = 65% of the selling price
        stock: Optional[float] = None            # how many are left in the shop AFTER this sale; None = N/A, count later
        itemId: Optional[int] = None             # it IS an item already in Supply: link to it instead of adding one

    @r.post("/requests/{uid}/accept")
    def accept_request(uid: int, body: AcceptIn, user=Depends(admin), conn=Depends(get_db)):
        """Accept a new item from a phone: it is added to Supply (or linked to an item already there) and the row becomes
        a normal, checked row of its receipt. It is NOT sold here: the sales person presses Update on the phone and says
        Cash or Bank. The stock typed is what is left after this sale, so the item gets that + the row's quantity
        (the phone's Update takes the row's quantity off)."""
        u = request_row(conn, uid)
        rc = conn.execute("SELECT * FROM receipts WHERE id=?", (u["receipt_id"],)).fetchone()
        if not rc:
            raise HTTPException(404, "Its receipt was deleted")
        if body.stock is not None and body.stock < 0:
            raise HTTPException(422, "The stock cannot be below 0")
        qty = u["req_qty"] or u["qty"] or 1
        new_item = body.itemId is None
        with conn:
            if not new_item:
                it = conn.execute("SELECT * FROM items WHERE id=? AND deleted_at IS NULL", (body.itemId,)).fetchone()
                if not it:
                    raise HTTPException(404, "That Supply item was not found")
                iid, name = it["id"], it["name"]
            else:
                name = _re_mod.sub(r"\s+", " ", (body.name or u["req_name"] or "")).strip()
                sell = body.sellPrice if body.sellPrice is not None else (u["req_price"] or 0)
                if len(name) < 2 or not sell or sell <= 0:
                    raise HTTPException(422, "A name and a selling price above 0 are needed")
                if conn.execute("SELECT 1 FROM products WHERE name=? COLLATE NOCASE", (name,)).fetchone() or \
                        conn.execute("SELECT 1 FROM items WHERE name=? COLLATE NOCASE", (name,)).fetchone():
                    raise HTTPException(409, f"\"{name}\" is already in Supply: choose it under \"It is already in Supply\" "
                                             "or give the new item another name")
                pid = conn.execute("INSERT INTO products(name, created) VALUES(?,?)", (name, now())).lastrowid
                cost = body.costPrice if (body.costPrice or 0) > 0 else None
                counted = body.stock is not None
                iid = conn.execute("""INSERT INTO items(name, unit, quantity, sell_price, cost_price, product_id, cost_auto,
                                                        qty_unknown) VALUES(?,?,?,?,?,?,?,?)""",
                                   (name, shop_db.unit_name(body.unit or u["req_unit"] or "pcs") or "pcs",
                                    (body.stock + qty) if counted else 0, sell,
                                    cost if cost else shop_db.assumed_cost(sell), pid, 0 if cost else 1, 0 if counted else 1)).lastrowid
            conn.execute("""INSERT INTO receipt_lines(receipt_id, ocr_text, item_id, score, qty, verified, price, item_text)
                            VALUES(?,?,?,100,?,1,?,?)""", (u["receipt_id"], u["ocr_text"], iid, qty, u["req_price"],
                                                           u["item_text"] or u["req_name"]))
            conn.execute("DELETE FROM receipt_unmatched WHERE id=?", (uid,))
        who = (conn.execute("SELECT COALESCE(display_name, username) FROM users WHERE id=?", (u["req_by"],)).fetchone()
               or ["the sales person"])[0]
        note(conn, user, "New item accepted", f"{name} from {who}: {qty:g} at {peso_txt(u['req_price'] or 0)}"
             + (f" · {body.stock:g} left" if new_item and body.stock is not None else ""))
        return {"itemId": iid, "sold": False, "receiptId": u["receipt_id"],
                "message": f"{name} is in Supply. It is back on {who}'s receipt: {who} presses Update on the phone to sell it "
                           f"(and says Cash or Bank)."}

    @r.delete("/requests/{uid}")
    def remove_request(uid: int, user=Depends(admin), conn=Depends(get_db)):
        """Remove a new item sent from a phone: the row leaves its receipt (nothing is added, nothing is sold)."""
        u = request_row(conn, uid)
        with conn:
            conn.execute("DELETE FROM receipt_unmatched WHERE id=?", (uid,))
        note(conn, user, "New item removed", f"{u['req_name']} ({u['req_qty']:g} at {peso_txt(u['req_price'] or 0)})")
        return {"removed": uid}

    class RowItemIn(BaseModel):
        itemId: int

    @r.post("/receipts/{rid}/rows/{kind}/{row_id}/item")
    def receipt_row_item(rid: int, kind: str, row_id: int, body: RowItemIn, user=Depends(admin), conn=Depends(get_db)):
        """Sales -> Receipt: this row IS that Supply item (usually one just added from the row with "Add to Supply").
        kind 'u' = a row the shop did not carry: it becomes a normal row of the receipt; kind 'l' = a row matched to the
        wrong item (not sold yet): it now points to the right one. The phone sees it at once (checked, ready for Update)."""
        rc = conn.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
        it = conn.execute("SELECT * FROM items WHERE id=? AND deleted_at IS NULL", (body.itemId,)).fetchone()
        if not rc or not it:
            raise HTTPException(404, "Receipt or item not found")
        if kind == "u":
            u = conn.execute("SELECT * FROM receipt_unmatched WHERE id=? AND receipt_id=?", (row_id, rid)).fetchone()
            if not u:
                raise HTTPException(404, "That row is not on this receipt any more")
            q_, p_ = (u["req_qty"], u["req_price"]) if u["req_at"] else (u["qty"], u["price"])   # sent from a phone: its numbers
            with conn:
                conn.execute("""INSERT INTO receipt_lines(receipt_id, ocr_text, item_id, score, qty, verified, price, item_text)
                                VALUES(?,?,?,100,?,1,?,?)""", (rid, u["ocr_text"], it["id"], q_, p_, u["item_text"] or u["req_name"]))
                conn.execute("DELETE FROM receipt_unmatched WHERE id=?", (row_id,))
            was = u["item_text"] or u["ocr_text"] or "a row"
        elif kind == "l":
            l_ = conn.execute("SELECT l.*, i.name FROM receipt_lines l JOIN items i ON i.id=l.item_id WHERE l.id=? AND l.receipt_id=?",
                              (row_id, rid)).fetchone()
            if not l_:
                raise HTTPException(404, "That row is not on this receipt any more")
            if l_["sold"]:
                raise HTTPException(409, "That row was already sold: it cannot be changed")
            with conn:
                conn.execute("UPDATE receipt_lines SET item_id=?, score=100, verified=1 WHERE id=?", (it["id"], row_id))
            was = l_["name"]
        else:
            raise HTTPException(422, "kind is u or l")
        note(conn, user, "Receipt row", f"{shop_db.receipt_label(rc['created'])}: {was} → {it['name']}"[:250])
        return receipt_detail(rid, user, conn)

    @r.post("/receipts/{rid}/read-again")
    def receipt_read_again(rid: int, user=Depends(admin), conn=Depends(get_db)):
        """Read a receipt photo again with the receipt reader (e.g. after adding its items to Supply). The rows and the
        delivery order made from it are refreshed. Not possible once part of it was sold."""
        if reread is None:
            raise HTTPException(503, "The receipt reader is not running")
        rc = conn.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
        if not rc:
            raise HTTPException(404, "Receipt not found")
        if conn.execute("SELECT 1 FROM receipt_lines WHERE receipt_id=? AND sold=1", (rid,)).fetchone():
            raise HTTPException(409, "Part of this receipt was already sold: it cannot be read again")
        if rc["status"] in ("queued", "processing"):
            raise HTTPException(409, "It is being read right now")
        f = UPLOAD_DIR / f"{rid}.jpg"
        if not f.exists() and rc["image"]:
            f = Path(rc["image"])
        if not f.exists():
            raise HTTPException(404, "The photo of this receipt is gone")
        with conn:
            conn.execute("UPDATE receipts SET status='queued', error=NULL WHERE id=?", (rid,))
        reread(rid, str(f))
        note(conn, user, "Read again", f"receipt {shop_db.receipt_label(rc['created'])}")
        return {"id": rid, "status": "queued"}

    @r.delete("/receipts/{rid}")
    def receipt_delete(rid: int, revert: bool = False, user=Depends(admin), conn=Depends(get_db)):
        """Sales -> a counter -> Receipt -> Delete. Removes the photo and its rows. When part of it was already sold the
        answer is 409 {"error": "has_sales", "total"}; ?revert=true then also undoes that sale (the stock goes back,
        the total leaves the sales). A delivery order still waiting from this photo goes too."""
        if drop_receipt is None:
            raise HTTPException(503, "Not available")
        rc = conn.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
        if not rc:
            raise HTTPException(404, "Receipt not found")
        who = conn.execute("SELECT COALESCE(display_name, username) FROM users WHERE id=?", (rc["user_id"],)).fetchone()
        out = drop_receipt(conn, rc, revert)
        if isinstance(out, dict):
            note(conn, user, "Receipt deleted", f"{shop_db.receipt_label(rc['created'])} of {who[0] if who else 'a phone'}"
                 + (f" (sale of {peso_txt(out['restored_total'])} undone)" if out.get("reverted_sales") else ""))
        return out

    @r.get("/receipts/{rid}/image")
    def receipt_image(rid: int, w: int = 0, user=Depends(admin_q), conn=Depends(get_db)):
        rc = conn.execute("SELECT image FROM receipts WHERE id=?", (rid,)).fetchone()
        if not rc:
            raise HTTPException(404, "Receipt not found")
        path = UPLOAD_DIR / rc["image"] if not str(rc["image"]).startswith("/") else None
        if path is None or not path.exists():
            path = UPLOAD_DIR / f"{rid}.jpg"
        if not path.exists():
            raise HTTPException(404, "Photo file is gone (cleaned up)")
        if w and 64 <= w <= 1600:
            thumb = THUMB_DIR / f"{rid}_{w}.jpg"
            if not thumb.exists():
                im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
                im.thumbnail((w, w * 4))
                im.save(thumb, "JPEG", quality=80)
            return FileResponse(thumb, media_type="image/jpeg")
        return FileResponse(path, media_type="image/jpeg")

    def range_start(rng):
        n = datetime.now()
        if rng == "today":
            return n.replace(hour=0, minute=0, second=0, microsecond=0)
        if rng == "week":
            return n - timedelta(days=7)
        if rng == "month":
            return n - timedelta(days=30)
        raise HTTPException(422, "range must be today, week or month")

    @r.get("/stats")
    def stats(range: str = "today", low_stock_at: float = 10, user=Depends(admin), conn=Depends(get_db)):
        start = range_start(range).isoformat(timespec="seconds")
        s = conn.execute("SELECT COALESCE(SUM(total),0) t, COALESCE(SUM(profit),0) p, COUNT(*) n FROM sales WHERE created>=?",
                         (start,)).fetchone()
        saved = conn.execute("SELECT COALESCE(SUM(savings),0) FROM stock_purchases WHERE bought_at>=?", (start,)).fetchone()[0]
        return {"range": range, "sales": s["t"], "profit": s["p"], "purchaseSavings": saved, "count": s["n"],
                "lowStock": low_stock(conn)}

    def low_stock(conn):
        """Items at stock level A (critical), B (low) or C (watch): the dashboard Priority and the restock list.
        buy = how many to buy so the item is above its highest level again (None when no level is set);
        total = buy x the standard cost (what that purchase costs at the usual price)."""
        low = []
        for x in conn.execute("SELECT * FROM items WHERE COALESCE(qty_unknown,0)=0 AND deleted_at IS NULL ORDER BY quantity"):
            lv = shop_db.stock_level(x["quantity"], x["level_a"], x["level_b"], x["level_c"])
            if lv:
                levels = [v for v in (x["level_a"], x["level_b"], x["level_c"]) if v is not None]
                q = x["quantity"] or 0
                buy = None
                if levels:
                    top = max(levels)
                    whole = float(top).is_integer() and float(q).is_integer()
                    buy = round(top - q + 1) if whole else round(top - q, 2)
                    buy = buy if buy > 0 else None
                low.append({"id": x["id"], "name": x["name"], "unit": x["unit"], "quantity": x["quantity"], "level": lv,
                            "costPrice": x["cost_price"], "sellPrice": x["sell_price"],
                            "levelA": x["level_a"], "levelB": x["level_b"], "levelC": x["level_c"], "buy": buy,
                            "total": round(buy * (x["cost_price"] or 0), 2) if buy else None})
        low.sort(key=lambda x: ("ABC".index(x["level"]), x["quantity"], x["name"].lower()))
        return low

    @r.get("/restock")
    def restock(user=Depends(admin), conn=Depends(get_db)):
        """Supply -> Restock list: the A / B / C items to buy again (printed at the end of the day)."""
        return {"at": now(), "items": low_stock(conn)}

    @r.get("/restock/download")
    def restock_download(user=Depends(admin_q), conn=Depends(get_db)):
        """The restock list as a CSV file (opens in Excel), with empty columns to write what was bought."""
        def num(v):                                     # quantities: 4, 2.5
            return "" if v is None else f"{round(v, 3):g}" if abs(v) < 1e6 else str(v)

        def money(v):                                   # pesos: always 2 decimals (no rounding of big totals)
            return "" if v is None else f"{v:.2f}"
        items = low_stock(conn)
        rows = [[x["level"], x["name"], x["unit"], num(x["quantity"]), num(x["levelA"]), num(x["levelB"]), num(x["levelC"]),
                 num(x["buy"]), money(x["costPrice"]), money(x["total"]), "", ""] for x in items]
        for lv in "ABC":
            part = [x["total"] or 0 for x in items if x["level"] == lv]
            if part:
                rows.append(["", f"TOTAL {lv}", "", "", "", "", "", "", "", money(sum(part)), "", ""])
        rows.append(["", "TOTAL (all)", "", "", "", "", "", "", "", money(sum(x["total"] or 0 for x in items)), "", ""])
        day = datetime.now().strftime("%Y-%m-%d")
        f = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        f.write(csv_bytes(["level", "item", "unit", "stock now", "A at", "B at", "C at", "buy (suggested)",
                           "standard cost", "total (buy x cost)", "bought qty", "price paid"], rows))
        f.close()
        return FileResponse(f.name, media_type="text/csv", filename=f"restock_list_{day}.csv",
                            background=BackgroundTask(lambda: __import__("os").remove(f.name)))

    @r.get("/sales-daily")
    def sales_daily(days: int = 5, user=Depends(admin), conn=Depends(get_db)):
        """Totals for each of the last N days (days with no sales show 0) - the dashboard chart."""
        days = max(1, min(days, 90))
        start = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        got = {x["d"]: x["t"] for x in conn.execute(
            "SELECT substr(created,1,10) d, SUM(total) t FROM sales WHERE created>=? GROUP BY d", (start.isoformat(timespec="seconds"),))}
        out = []
        for i in range(days):
            d = start + timedelta(days=i)
            out.append({"day": d.strftime("%b %d"), "sales": got.get(d.strftime("%Y-%m-%d"), 0)})
        return out

    @r.get("/sales-series")
    def sales_series(group: str = "month", count: int = 6, user=Depends(admin), conn=Depends(get_db)):
        """Totals per sales person per day / week (Monday) / month, the last `count` periods (the Sales chart)."""
        count = max(1, min(count, 60))
        t0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if group == "day":
            keys = [(t0 - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count - 1, -1, -1)]
            expr = "substr(s.created,1,10)"
        elif group == "week":
            monday = t0 - timedelta(days=t0.weekday())
            keys = [(monday - timedelta(weeks=i)).strftime("%Y-%m-%d") for i in range(count - 1, -1, -1)]
            expr = "date(substr(s.created,1,10), '-' || ((strftime('%w', substr(s.created,1,10)) + 6) % 7) || ' days')"
        elif group == "month":
            y, m = t0.year, t0.month
            keys = []
            for i in range(count - 1, -1, -1):
                mm = m - i
                yy = y + (mm - 1) // 12
                keys.append(f"{yy}-{(mm - 1) % 12 + 1:02d}")
            expr = "substr(s.created,1,7)"
        else:
            raise HTTPException(422, "group must be day, week or month")
        rows = conn.execute(f"""SELECT {expr} AS k, s.user_id AS uid, SUM(s.total) AS total FROM sales s
                                WHERE s.user_id IS NOT NULL AND {expr} >= ? GROUP BY k, uid""", (keys[0],)).fetchall()
        return {"group": group, "keys": keys, "rows": [dict(x) for x in rows if x["k"] in keys]}

    @r.get("/sales-monthly")
    def sales_monthly(months: int = 6, user=Depends(admin), conn=Depends(get_db)):
        """Per person per month totals (actual history, used by the Sales chart)."""
        rows = conn.execute("""SELECT strftime('%Y-%m', s.created) AS m, u.id AS uid, COALESCE(u.display_name,u.username) AS name,
                                      SUM(s.total) AS total
                               FROM sales s JOIN users u ON u.id=s.user_id
                               GROUP BY m, u.id ORDER BY m DESC LIMIT ?""", (months * 50,)).fetchall()
        return [dict(x) for x in rows]

    # ---------------- bulk / VIP orders ----------------
    def unit_kg(conn, name):
        r_ = conn.execute("SELECT to_kg FROM units WHERE name=?", (shop_db.unit_name(name),)).fetchone()
        return r_[0] if r_ else None

    def transport_dict(t, conn=None):
        v, u = t["capacity_value"], t["capacity_unit"]
        if v is None and t["capacity"]:
            v, u = shop_db.split_capacity(t["capacity"])
        cap_kg = (v * (unit_kg(conn, u) or 0)) if (conn is not None and v) else None
        return {"id": t["id"], "truckName": t["truck_name"], "plate": t["plate"] or "", "capacityValue": v,
                "capacityUnit": u or "", "capacity": f"{v:g} {u}" if v else (t["capacity"] or ""),
                "capacityKg": cap_kg or None, "cost": t["cost"]}

    def order_dict(conn, o):
        oid = o["id"]
        items = [{"id": x["id"], "name": x["name"], "qty": x["qty"], "unit": x["unit"] or "", "price": x["price"],
                  "delivered": bool(x["delivered"]), "itemId": x["item_id"], "kgPerUnit": x["kg_per_unit"],
                  "stockTaken": x["stock_taken"]}       # what this row took out of Supply (None = saved before r16)
                 for x in conn.execute("SELECT * FROM bulk_order_items WHERE order_id=? ORDER BY id", (oid,))]
        runs = [run_dict(conn, x) for x in conn.execute("SELECT * FROM bulk_order_runs WHERE order_id=? ORDER BY id", (oid,))]
        m = order_money(conn, o, items=sum(i["qty"] * i["price"] for i in items), runs=runs)
        return {"id": oid, "customer": o["customer"], "address": o["address"] or "", "items": items,
                "payments": [{"id": f"b{x['id']}", "date": x["date"], "amount": x["amount"], "note": x["note"], "method": x["method"]}
                             for x in sorted(order_pays(conn, oid), key=lambda x: x["id"])],
                "startDate": o["start_date"] or "", "endDate": o["end_date"] or "",
                "deliveryRuns": runs, "status": o["status"], "completeDate": o["completed_date"] or "",
                "itemsTotal": m["items"], "deliveryCost": m["delivery"], "chargeDelivery": m["charged"],
                "deliveryCharge": m["delivery"] if m["charged"] else 0, "total": m["total"],
                "fromReceipt": order_from_receipt(conn, oid)}   # made from a phone's delivery receipt: takes no stock

    RUN_MODES = ("truck", "pickup", "other")      # our truck | the customer picks it up himself | someone else delivers

    def run_mode(x):
        m = (x["mode"] if "mode" in x.keys() else None) or "truck"
        return m if m in RUN_MODES else "truck"

    # Round 18: a truck trip of its own (no order): bulk_order_runs.order_id NULL + purpose / place / note / time
    TRIP_PURPOSES = {"deliver": "Deliver", "get": "Get materials", "personal": "Personal use"}

    def col(x, k):
        return x[k] if k in x.keys() else None

    def trip_label(x):
        """'Get materials: Puerto Princesa' for a trip of its own"""
        what = TRIP_PURPOSES.get(col(x, "purpose") or "", "Truck trip")
        return f"{what}: {col(x, 'place')}" if col(x, "place") else what

    def run_cost(conn, x):
        """what one run costs: our truck = its cost per trip x trips; someone else = their price per trip x trips;
        the customer picking it up = nothing"""
        mode, trips = run_mode(x), max(1, x["trips"] or 1)
        if mode == "pickup":
            return 0.0
        if mode == "other":
            return round((x["carrier_cost"] or 0) * trips, 2)
        t = conn.execute("SELECT cost FROM transports WHERE id=?", (x["transport_id"],)).fetchone() if x["transport_id"] else None
        return round(((t[0] or 0) if t else 0) * trips, 2)

    def order_money(conn, o, items=None, runs=None):
        """{'items', 'delivery', 'charged', 'total'}: the order's items, its delivery cost, whether the customer pays the
        delivery (then the total = items + delivery) and the total the customer pays"""
        if items is None:
            items = conn.execute("SELECT COALESCE(SUM(qty*price),0) FROM bulk_order_items WHERE order_id=?", (o["id"],)).fetchone()[0]
        if runs is None:
            delivery = sum(run_cost(conn, x) for x in conn.execute("SELECT * FROM bulk_order_runs WHERE order_id=?", (o["id"],)))
        else:
            delivery = sum(r["cost"] for r in runs)
        charged = bool(o["charge_delivery"]) if "charge_delivery" in o.keys() else False
        delivery = round(delivery, 2)
        return {"items": round(items, 2), "delivery": delivery, "charged": charged,
                "total": round(items + (delivery if charged else 0), 2)}

    def run_dict(conn, x):
        """one delivery run: who delivers (mode), with our truck its crew: driver (the person in charge of the delivery)
        + helpers (companions); cost = what this run costs (see run_cost)"""
        mode = run_mode(x)
        drv = conn.execute("SELECT id, name, temporary FROM employees WHERE id=?", (x["driver_id"],)).fetchone() \
            if x["driver_id"] and mode == "truck" else None
        helpers = [] if mode != "truck" else [{"id": h["id"], "name": h["name"], "temporary": bool(h["temporary"])} for h in conn.execute(
            """SELECT e.id, e.name, e.temporary FROM run_crew c JOIN employees e ON e.id=c.employee_id
               WHERE c.run_id=? ORDER BY e.name""", (x["id"],))]
        trip = col(x, "order_id") is None
        return {"id": x["id"], "mode": mode, "transportId": x["transport_id"] if mode == "truck" else None,
                "carrier": x["carrier"] or "", "carrierCost": x["carrier_cost"] or 0, "trips": x["trips"], "date": x["date"] or "",
                "driverId": drv["id"] if drv else None, "driverName": drv["name"] if drv else "",
                "helperIds": [h["id"] for h in helpers], "helpers": helpers, "done": bool(x["done"]), "cost": run_cost(conn, x),
                "isTrip": trip, "purpose": (col(x, "purpose") or "deliver") if trip else "order", "place": col(x, "place") or "",
                "note": col(x, "note") or "", "time": col(x, "time") or "", "label": trip_label(x) if trip else ""}

    def set_crew(conn, run_id, driver_id, helper_ids):
        """driver + helpers of one run (unknown people are ignored; the driver is never also a helper)"""
        if driver_id is not None and not conn.execute("SELECT 1 FROM employees WHERE id=?", (driver_id,)).fetchone():
            driver_id = None
        conn.execute("UPDATE bulk_order_runs SET driver_id=? WHERE id=?", (driver_id, run_id))
        conn.execute("DELETE FROM run_crew WHERE run_id=?", (run_id,))
        for h in dict.fromkeys(helper_ids or []):
            if h != driver_id and conn.execute("SELECT 1 FROM employees WHERE id=?", (h,)).fetchone():
                conn.execute("INSERT OR IGNORE INTO run_crew(run_id, employee_id) VALUES(?,?)", (run_id, h))

    def get_order(conn, oid):
        o = conn.execute("SELECT * FROM bulk_orders WHERE id=?", (oid,)).fetchone()
        if not o:
            raise HTTPException(404, "Order not found")
        return o

    @r.get("/orders")
    def orders(user=Depends(admin), conn=Depends(get_db)):
        rows = conn.execute("SELECT * FROM bulk_orders ORDER BY id").fetchall()
        active = [order_dict(conn, o) for o in rows if o["status"] == "active"]
        history = [order_dict(conn, o) for o in reversed(rows) if o["status"] == "done"]
        return {"active": active,
                "history": [{"id": h["id"], "customer": h["customer"], "total": h["total"], "completeDate": h["completeDate"]}
                            for h in history],
                "transports": [transport_dict(t, conn) for t in conn.execute("SELECT * FROM transports ORDER BY id")],
                "units": units_list(conn)}

    def units_list(conn):
        return [{"name": u["name"], "toKg": u["to_kg"]} for u in conn.execute("SELECT * FROM units ORDER BY id")]

    class UnitIn(BaseModel):
        name: str
        toKg: Optional[float] = None

    @r.get("/units")
    def units(user=Depends(admin), conn=Depends(get_db)):
        return units_list(conn)

    @r.post("/units")
    def add_unit(body: UnitIn, user=Depends(admin), conn=Depends(get_db)):
        """'+ New unit' in a unit drop-down. toKg only for real weight units (e.g. 'quintal' = 100)."""
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Unit name is required")
        with conn:
            conn.execute("INSERT OR IGNORE INTO units(name, to_kg) VALUES(?,?)", (name, body.toKg))
        note(conn, user, "New unit", name)
        return units_list(conn)

    class OrderItemIn(BaseModel):
        id: Optional[int] = None          # set when EDITING an existing row (keeps its "delivered" tick)
        name: str
        qty: float = 0
        unit: str = ""
        price: float = 0
        itemId: Optional[int] = None      # picked from the inventory suggestions
        kgPerUnit: Optional[float] = None

    class RunIn(BaseModel):
        id: Optional[int] = None          # set when EDITING an existing run (keeps its crew and its "delivered" tick)
        mode: str = "truck"               # truck (ours) | pickup (the customer's own vehicle) | other (someone else)
        carrier: str = ""                 # pickup: who / which vehicle comes (optional); other: who delivers it
        carrierCost: float = 0            # other: what they charge per trip
        transportId: Optional[int] = None
        trips: int = 1
        date: str = ""
        driverId: Optional[int] = None    # the person in charge of this delivery
        helperIds: Optional[list[int]] = None   # companions (labor / temporary helpers); None = keep as it is
        done: Optional[bool] = None

    class OrderIn(BaseModel):
        customer: str
        address: str = ""                 # where to deliver (read from a delivery receipt, or typed)
        items: list[OrderItemIn] = []
        paid: float = 0
        paidBy: str = "cash"              # the down payment came in as cash | bank
        startDate: str = ""
        endDate: str = ""
        deliveryRuns: list[RunIn] = []
        chargeDelivery: Optional[bool] = None    # the customer pays the delivery (total = items + delivery); new orders: yes

    # ---- an order takes its items out of Supply ----
    # A row picked from Supply (itemId) in the item's own unit takes its quantity from items.quantity when the order is
    # saved; editing the order takes / gives back only the difference; deleting it gives back what was not delivered.
    # More than Supply has is refused (409, detail.short) so the form can say "this is our only supply".
    # Not taken: typed rows (not from Supply), another unit than the item's (a roll item ordered in meters), items whose
    # stock was never counted (N/A), and orders made from a phone's DELIVERY receipt (the phone's Update of that receipt
    # sells it and takes the stock: taking it here too would count it twice).
    def takes_stock(row_unit, it):
        if it is None or it["qty_unknown"]:
            return False
        u = shop_db.unit_name(row_unit or "").lower()
        return not u or u == shop_db.unit_name(it["unit"] or "").lower()

    def order_from_receipt(conn, oid):
        return bool(oid) and conn.execute("SELECT 1 FROM delivery_drafts WHERE order_id=?", (oid,)).fetchone() is not None

    def plan_stock(conn, oid, items, take=True):
        """(takes, before, short): takes[k] = (item_id, qty) row k takes; before = {item_id: what the order took so far};
        short = [{itemId, name, unit, want, have}] for items Supply has too little of (what this order already took
        counts as still available to it)."""
        before = {}
        if oid:
            for x in conn.execute("SELECT item_id, COALESCE(stock_taken,0) t FROM bulk_order_items WHERE order_id=? AND item_id IS NOT NULL",
                                  (oid,)):
                before[x["item_id"]] = before.get(x["item_id"], 0.0) + (x["t"] or 0)
        takes, want, rows = [], {}, {}
        for i in items:
            it = conn.execute("SELECT * FROM items WHERE id=? AND deleted_at IS NULL", (i.itemId,)).fetchone() \
                if (take and i.itemId) else None
            q = max(0.0, float(i.qty or 0))
            if it is not None and q > 0 and takes_stock(i.unit, it):
                takes.append((it["id"], q))
                want[it["id"]] = want.get(it["id"], 0.0) + q
                rows[it["id"]] = it
            else:
                takes.append((i.itemId if i.itemId else None, 0.0))
        short = []
        for iid, w in want.items():
            have = (rows[iid]["quantity"] or 0) + before.get(iid, 0.0)
            if w > have + 1e-9:
                short.append({"itemId": iid, "name": rows[iid]["name"], "unit": rows[iid]["unit"], "want": round(w, 3),
                              "have": round(max(0.0, have), 3)})
        return takes, before, short

    def not_enough_error(short):
        one = short[0]
        msg = (f"This is our only supply: {one['name']} has {one['have']:g} {one['unit']} left, the order asks for "
               f"{one['want']:g}" + (f" (and {len(short) - 1} more item(s) are short)" if len(short) > 1 else ""))
        return HTTPException(409, {"message": msg, "short": short})

    def write_order_rows(conn, oid, items, runs, take=True):
        """Insert new rows, update edited rows (their 'delivered' tick stays), delete rows that were removed; runs are
        replaced. The rows' quantities come out of Supply (see takes_stock); raises 409 when Supply has too little."""
        takes, before, short = plan_stock(conn, oid, items, take)
        if short:
            raise not_enough_error(short)
        now_take = {}
        for iid, t in takes:
            if iid and t > 0:
                now_take[iid] = now_take.get(iid, 0.0) + t
        for iid in set(before) | set(now_take):          # take / give back only the difference
            delta = now_take.get(iid, 0.0) - before.get(iid, 0.0)
            if abs(delta) > 1e-9:
                conn.execute("UPDATE items SET quantity = quantity - ? WHERE id=?", (delta, iid))
        keep = []
        for i, (_iid, t) in zip(items, takes):
            vals = (i.name.strip(), i.qty, shop_db.unit_name(i.unit), i.price, i.itemId, i.kgPerUnit, t)
            if i.id and conn.execute("SELECT 1 FROM bulk_order_items WHERE id=? AND order_id=?", (i.id, oid)).fetchone():
                conn.execute("""UPDATE bulk_order_items SET name=?, qty=?, unit=?, price=?, item_id=?, kg_per_unit=?, stock_taken=?
                                WHERE id=?""", (*vals, i.id))
                keep.append(i.id)
            else:
                keep.append(conn.execute("""INSERT INTO bulk_order_items(order_id,name,qty,unit,price,item_id,kg_per_unit,stock_taken)
                                            VALUES(?,?,?,?,?,?,?,?)""", (oid, *vals)).lastrowid)
        conn.execute(f"DELETE FROM bulk_order_items WHERE order_id=? AND id NOT IN ({','.join('?' * len(keep))})", (oid, *keep))
        keep_runs = []
        for run in runs:
            mode = run.mode if run.mode in RUN_MODES else "truck"
            if mode == "truck" and not run.transportId:
                continue                          # our truck but none picked: nothing to plan
            tid = run.transportId if mode == "truck" else None
            carrier = (run.carrier or "").strip()[:80] or None if mode != "truck" else None
            ccost = max(0.0, run.carrierCost or 0) if mode == "other" else None
            old = conn.execute("SELECT * FROM bulk_order_runs WHERE id=? AND order_id=?", (run.id, oid)).fetchone() if run.id else None
            done = (1 if run.done else 0) if run.done is not None else (old["done"] if old else 0)
            if old:
                conn.execute("""UPDATE bulk_order_runs SET transport_id=?, trips=?, date=?, done=?, mode=?, carrier=?, carrier_cost=?
                                WHERE id=?""", (tid, max(1, run.trips), clean(run.date), done, mode, carrier, ccost, old["id"]))
                rid = old["id"]
            else:
                rid = conn.execute("""INSERT INTO bulk_order_runs(order_id,transport_id,trips,date,done,mode,carrier,carrier_cost)
                                      VALUES(?,?,?,?,?,?,?,?)""", (oid, tid, max(1, run.trips), clean(run.date), done, mode,
                                                                   carrier, ccost)).lastrowid
            keep_runs.append(rid)
            if mode != "truck":                   # the customer / someone else delivers: no driver, no helpers of ours
                set_crew(conn, rid, None, [])
                continue
            helpers = run.helperIds if run.helperIds is not None else \
                [x[0] for x in conn.execute("SELECT employee_id FROM run_crew WHERE run_id=?", (rid,))]
            set_crew(conn, rid, run.driverId, helpers)
        gone = f"order_id=? AND id NOT IN ({','.join('?' * len(keep_runs))})" if keep_runs else "order_id=?"
        conn.execute(f"DELETE FROM run_crew WHERE run_id IN (SELECT id FROM bulk_order_runs WHERE {gone})", (oid, *keep_runs))
        conn.execute(f"DELETE FROM bulk_order_runs WHERE {gone}", (oid, *keep_runs))

    @r.put("/orders/{oid}")
    def edit_order(oid: int, body: OrderIn, user=Depends(admin), conn=Depends(get_db)):
        """Edit an order (customer changed the quantity, added an item, another truck...). Payments are not touched here."""
        o = get_order(conn, oid)
        items = [i for i in body.items if i.name.strip()]
        if not body.customer.strip() or not items:
            raise HTTPException(422, "A customer name and at least one item are required")
        before = order_dict(conn, o)["total"]
        taken0 = stock_taken_txt(conn, oid)
        with conn:
            conn.execute("UPDATE bulk_orders SET customer=?, address=?, start_date=?, end_date=? WHERE id=?",
                         (body.customer.strip(), body.address.strip() or None, clean(body.startDate), clean(body.endDate), oid))
            if body.chargeDelivery is not None:
                conn.execute("UPDATE bulk_orders SET charge_delivery=? WHERE id=?", (1 if body.chargeDelivery else 0, oid))
            write_order_rows(conn, oid, items, body.deliveryRuns, take=not order_from_receipt(conn, oid))
        after = order_dict(conn, get_order(conn, oid))
        taken1 = stock_taken_txt(conn, oid)
        note(conn, user, "Order edited", f"{after['customer']}: total {peso_txt(before)} → {peso_txt(after['total'])}"
                                         + (f" · from Supply: {taken1 or 'nothing'}" if taken1 != taken0 else ""))
        return after

    def stock_taken_txt(conn, oid):
        """'50 liters Boysen Flat Latex White (Liter), 2 pcs Paint Tray': what the order took out of Supply"""
        return ", ".join(f"{x['stock_taken']:g} {x['unit'] or ''} {x['name']}".replace("  ", " ") for x in conn.execute(
            "SELECT name, unit, stock_taken FROM bulk_order_items WHERE order_id=? AND COALESCE(stock_taken,0) > 0 ORDER BY id", (oid,)))

    def create_order(conn, body, user, extra=None, take=True):
        """a new bulk order; `extra(conn, oid)` runs inside the same transaction (e.g. a delivery draft marked done).
        take=False: the rows do not come out of Supply (an order made from a phone's delivery receipt)"""
        items = [i for i in body.items if i.name.strip()]
        if not body.customer.strip() or not items:
            raise HTTPException(422, "A customer name and at least one item are required")
        with conn:
            oid = conn.execute("""INSERT INTO bulk_orders(customer,address,start_date,end_date,status,created,charge_delivery)
                                  VALUES(?,?,?,?,'active',?,?)""",
                               (body.customer.strip(), body.address.strip() or None, clean(body.startDate), clean(body.endDate),
                                now(), 0 if body.chargeDelivery is False else 1)).lastrowid
            write_order_rows(conn, oid, items, body.deliveryRuns, take=take)
            if body.paid > 0:
                record_order_payment(conn, {"id": oid, "customer": body.customer.strip()}, body.paid, today(), body.paidBy,
                                     "Down payment")
            if extra:
                extra(conn, oid)
        out = order_dict(conn, get_order(conn, oid))
        taken = stock_taken_txt(conn, oid)
        note(conn, user, "Bulk order", f"{body.customer.strip()}: {len(items)} item(s), {peso_txt(out['itemsTotal'])}"
                                       + (f" + delivery {peso_txt(out['deliveryCharge'])}" if out["deliveryCharge"] else "")
                                       + (f" · from Supply: {taken}" if taken else ""))
        return out

    @r.post("/orders")
    def add_order(body: OrderIn, user=Depends(admin), conn=Depends(get_db)):
        return create_order(conn, body, user)

    @r.post("/orders/{oid}/items/{iid}/toggle")
    def toggle_delivered(oid: int, iid: int, user=Depends(admin), conn=Depends(get_db)):
        o = get_order(conn, oid)
        with conn:
            n = conn.execute("UPDATE bulk_order_items SET delivered = 1 - delivered WHERE id=? AND order_id=?", (iid, oid)).rowcount
        if not n:
            raise HTTPException(404, "Item not found")
        it = conn.execute("SELECT * FROM bulk_order_items WHERE id=?", (iid,)).fetchone()
        note(conn, user, "Delivered" if it["delivered"] else "Not delivered", f"{o['customer']}: {it['qty']:g} {it['unit'] or ''} {it['name']}")
        return order_dict(conn, o)

    class OrderPayIn(LogIn):
        method: str = "cash"              # cash | bank (shows in Payments -> Cash payments / Bank payments)

    @r.post("/orders/{oid}/payments")
    def order_payment(oid: int, body: OrderPayIn, user=Depends(admin), conn=Depends(get_db)):
        o = get_order(conn, oid)
        if body.amount <= 0:
            raise HTTPException(422, "amount must be above 0")
        if body.method not in ("cash", "bank"):
            raise HTTPException(422, "method is cash or bank")
        with conn:
            record_order_payment(conn, o, body.amount, clean(body.date) or today(), body.method, body.note or "")
        note(conn, user, "Order payment", f"{o['customer']}: {peso_txt(body.amount)} ({body.method})")
        return order_dict(conn, o)

    @r.post("/orders/{oid}/complete")
    def complete_order(oid: int, user=Depends(admin), conn=Depends(get_db)):
        d = order_dict(conn, get_order(conn, oid))
        paid = sum(p["amount"] for p in d["payments"])
        if not d["items"] or not all(i["delivered"] for i in d["items"]) or d["total"] - paid > 0.005:
            raise HTTPException(409, "Deliver every item and collect the full payment first")
        with conn:
            conn.execute("UPDATE bulk_orders SET status='done', completed_date=? WHERE id=?", (today(), oid))
        note(conn, user, "Order done", f"{d['customer']}: {peso_txt(d['total'])}")
        return {"id": oid, "customer": d["customer"], "total": d["total"], "completeDate": today()}

    class FinishIn(BaseModel):
        method: str = "cash"              # how the rest was paid: cash | bank
        date: Optional[str] = None        # the day it was paid (default today)

    @r.post("/orders/{oid}/finish")
    def finish_order(oid: int, body: FinishIn, user=Depends(admin), conn=Depends(get_db)):
        """'Complete delivery & payment': in one step every item is marked delivered (and every trip done), the unpaid
        balance is recorded as paid (Cash or Bank, today), and the order is Done (it moves to the order history)."""
        o = get_order(conn, oid)
        if o["status"] != "active":
            raise HTTPException(409, "This order is already done")
        if body.method not in ("cash", "bank"):
            raise HTTPException(422, "method is cash or bank")
        d = order_dict(conn, o)
        if not d["items"]:
            raise HTTPException(409, "This order has no items")
        paid = sum(p_["amount"] for p_ in d["payments"])
        rest = round(d["total"] - paid, 2)
        day = clean(body.date) or today()
        delivered_now = sum(1 for i in d["items"] if not i["delivered"])
        with conn:
            conn.execute("UPDATE bulk_order_items SET delivered=1 WHERE order_id=?", (oid,))
            conn.execute("UPDATE bulk_order_runs SET done=1 WHERE order_id=?", (oid,))
            if rest > 0.005:
                record_order_payment(conn, o, rest, day, body.method, "the rest, paid when the order was finished")
            conn.execute("UPDATE bulk_orders SET status='done', completed_date=? WHERE id=?", (today(), oid))
        note(conn, user, "Order done", f"{d['customer']}: {peso_txt(d['total'])}"
             + (f" · {delivered_now} item(s) marked delivered" if delivered_now else "")
             + (f" · rest {peso_txt(rest)} paid ({body.method})" if rest > 0.005 else ""))
        return {"id": oid, "customer": d["customer"], "total": d["total"], "completeDate": today(),
                "paidNow": rest if rest > 0.005 else 0, "deliveredNow": delivered_now}

    @r.delete("/orders/{oid}")
    def delete_order(oid: int, user=Depends(admin), conn=Depends(get_db)):
        o = get_order(conn, oid)
        back = [(x["item_id"], x["stock_taken"], x["unit"], x["name"]) for x in conn.execute(
            """SELECT item_id, stock_taken, unit, name FROM bulk_order_items WHERE order_id=? AND item_id IS NOT NULL
               AND COALESCE(stock_taken,0) > 0 AND delivered=0""", (oid,))]
        with conn:
            for iid, q, _u, _n in back:                   # what was not delivered yet goes back to Supply
                conn.execute("UPDATE items SET quantity = quantity + ? WHERE id=?", (q, iid))
            conn.execute("DELETE FROM run_crew WHERE run_id IN (SELECT id FROM bulk_order_runs WHERE order_id=?)", (oid,))
            conn.execute("UPDATE delivery_drafts SET order_id=NULL WHERE order_id=?", (oid,))
            for t in ("bulk_order_items", "bulk_order_payments", "bulk_order_runs"):
                conn.execute(f"DELETE FROM {t} WHERE order_id=?", (oid,))
            # its cash / bank payment lines go with it (a mistake); a line of an old bank passbook only loses the link
            conn.execute("DELETE FROM pay_entries WHERE order_id=? AND account_id IS NULL", (oid,))
            conn.execute("UPDATE pay_entries SET order_id=NULL, order_payment_id=NULL, note=TRIM(COALESCE(note,'') || ?) WHERE order_id=?",
                         (f" (order of {o['customer']}, deleted)", oid))
            conn.execute("DELETE FROM bulk_orders WHERE id=?", (oid,))
        note(conn, user, "Deleted order", o["customer"] + (" · back to Supply: " + ", ".join(
            f"{q:g} {u or ''} {n}".replace("  ", " ") for _i, q, u, n in back) if back else ""))
        return {"deleted": oid, "backToSupply": len(back)}

    # ---------------- delivery crew + Planner ----------------
    # Every truck run has a driver (the person in charge of the delivery) and helpers (companions). The driver list is
    # the employees whose job says "driver"; the helper list is the labor (labor / helper / pahinante...) plus the
    # TEMPORARY workers of that day. Busy = already on another run that day. Planner page: deliveries per day, who goes
    # with whom, how many helpers are still free, so the owner knows when to hire temporary help.
    import re as _re_crew
    DRIVER_RE = _re_crew.compile(r"driver|chauffeur", _re_crew.I)
    LABOR_RE = _re_crew.compile(r"labor|labour|helper|pahinante|porter|loader|utility|crew|kargador", _re_crew.I)

    def busy_on(conn, d, skip_run=None):
        """employee id -> [{runId, customer, role}] for the runs on day d (not counting skip_run)"""
        busy = {}
        if not d:
            return busy
        for x in conn.execute("""SELECT r.id, r.driver_id, r.purpose, r.place, r.order_id, o.customer FROM bulk_order_runs r
                                 LEFT JOIN bulk_orders o ON o.id=r.order_id
                                 WHERE r.date=? AND (o.status='active' OR (r.order_id IS NULL AND COALESCE(r.done,0)=0))""", (d,)):
            if x["id"] == skip_run:
                continue
            x = {**dict(x), "customer": x["customer"] or trip_label(x)}
            if x["driver_id"]:
                busy.setdefault(x["driver_id"], []).append({"runId": x["id"], "customer": x["customer"], "role": "driver"})
            for (h,) in conn.execute("SELECT employee_id FROM run_crew WHERE run_id=?", (x["id"],)):
                busy.setdefault(h, []).append({"runId": x["id"], "customer": x["customer"], "role": "helper"})
        return busy

    def crew_pool(conn, d=None, skip_run=None):
        """who can drive / help on day d. Temporary workers count on a past day only if they were checked in that day;
        for today and later days every temporary worker still on the list counts."""
        t = today()
        present = {x[0] for x in conn.execute("SELECT employee_id FROM attendance WHERE date=?", (d,))} if d else set()
        busy = busy_on(conn, d, skip_run)
        drivers, helpers, others = [], [], []
        for e in conn.execute("SELECT * FROM employees ORDER BY name").fetchall():
            on_day = e["id"] in present
            if e["temporary"]:
                if not (on_day or (e["active"] and (not d or d >= t))):
                    continue
            elif not e["active"] and not on_day:
                continue
            row = {"id": e["id"], "name": e["name"], "position": e["position"] or "", "temporary": bool(e["temporary"]),
                   "hasPhoto": bool(e["photo"]), "present": on_day, "busy": busy.get(e["id"], [])}
            if DRIVER_RE.search(e["position"] or ""):
                drivers.append(row)
            elif e["temporary"] or LABOR_RE.search(e["position"] or ""):
                helpers.append(row)
            else:                                   # any other staff can be sent too (round 18): sales people, office...
                others.append(row)
        return {"date": d, "drivers": drivers, "helpers": helpers, "others": others, "isPast": bool(d and d < t), "isToday": d == t}

    @r.get("/crew")
    def crew(date: Optional[str] = None, run: Optional[int] = None, user=Depends(admin), conn=Depends(get_db)):
        """the driver and helper drop-downs of a truck run (Sales -> Add Order, Planner)"""
        return crew_pool(conn, check_day(date) if date else None, run)

    class RunPatch(BaseModel):
        date: Optional[str] = None
        mode: Optional[str] = None        # truck | pickup | other (who delivers)
        carrier: Optional[str] = None
        carrierCost: Optional[float] = None
        transportId: Optional[int] = None
        trips: Optional[int] = None
        driverId: Optional[int] = None
        helperIds: Optional[list[int]] = None
        done: Optional[bool] = None
        purpose: Optional[str] = None     # a trip of its own: deliver | get | personal
        place: Optional[str] = None       # where the truck goes
        note: Optional[str] = None
        time: Optional[str] = None        # "08:00"

    @r.put("/runs/{rid}")
    def update_run(rid: int, body: RunPatch, user=Depends(admin), conn=Depends(get_db)):
        """Planner: change one run (day, truck, trips, driver, helpers, delivered; for a trip of its own also the purpose,
        place, note and time). Only the fields sent change."""
        x = conn.execute("""SELECT r.*, COALESCE(o.customer, '') customer FROM bulk_order_runs r LEFT JOIN bulk_orders o
                            ON o.id=r.order_id WHERE r.id=?""", (rid,)).fetchone()
        if not x:
            raise HTTPException(404, "Delivery run not found")
        x = {**dict(x), "customer": x["customer"] or trip_label(x)}
        sent = fields_set(body)
        with conn:
            if "mode" in sent and body.mode:
                if body.mode != "truck":                      # round 17: deliveries are by our own truck only
                    raise HTTPException(422, "Deliveries are by our own truck only")
                if body.mode == "truck":                      # back to our own truck: it needs a truck
                    tid = body.transportId or x["transport_id"] or \
                        (conn.execute("SELECT id FROM transports ORDER BY id LIMIT 1").fetchone() or [None])[0]
                    if not tid:
                        raise HTTPException(409, "There is no truck yet: add one in Sales -> Add Order")
                    conn.execute("UPDATE bulk_order_runs SET mode='truck', transport_id=?, carrier=NULL, carrier_cost=NULL WHERE id=?",
                                 (tid, rid))
                else:                                         # the customer / someone else: no truck, no crew of ours
                    conn.execute("UPDATE bulk_order_runs SET mode=?, transport_id=NULL WHERE id=?", (body.mode, rid))
                    set_crew(conn, rid, None, [])
            if "carrier" in sent:
                conn.execute("UPDATE bulk_order_runs SET carrier=? WHERE id=?", ((body.carrier or "").strip()[:80] or None, rid))
            if "carrierCost" in sent:
                conn.execute("UPDATE bulk_order_runs SET carrier_cost=? WHERE id=?", (max(0.0, body.carrierCost or 0), rid))
            if "date" in sent:
                conn.execute("UPDATE bulk_order_runs SET date=? WHERE id=?", (check_day(body.date) if body.date else None, rid))
            if "transportId" in sent and body.transportId:
                if not conn.execute("SELECT 1 FROM transports WHERE id=?", (body.transportId,)).fetchone():
                    raise HTTPException(404, "Truck not found")
                conn.execute("UPDATE bulk_order_runs SET transport_id=?, mode='truck' WHERE id=?", (body.transportId, rid))
            if "trips" in sent and body.trips:
                conn.execute("UPDATE bulk_order_runs SET trips=? WHERE id=?", (max(1, body.trips), rid))
            if "done" in sent and body.done is not None:
                conn.execute("UPDATE bulk_order_runs SET done=? WHERE id=?", (1 if body.done else 0, rid))
                if body.done and x["order_id"] and not conn.execute("SELECT 1 FROM bulk_order_runs WHERE order_id=? AND done=0",
                                                                    (x["order_id"],)).fetchone():
                    # the last trip of the order was delivered: every item of the order is delivered
                    conn.execute("UPDATE bulk_order_items SET delivered=1 WHERE order_id=?", (x["order_id"],))
            if x["order_id"] is None:                     # a trip of its own: what / where / note / time
                if "purpose" in sent and body.purpose:
                    if body.purpose not in TRIP_PURPOSES:
                        raise HTTPException(422, "purpose is deliver, get or personal")
                    conn.execute("UPDATE bulk_order_runs SET purpose=? WHERE id=?", (body.purpose, rid))
                for k in ("place", "note"):
                    if k in sent:
                        conn.execute(f"UPDATE bulk_order_runs SET {k}=? WHERE id=?", ((getattr(body, k) or "").strip()[:160] or None, rid))
                if "time" in sent:
                    conn.execute("UPDATE bulk_order_runs SET time=? WHERE id=?", (clean_time(body.time), rid))
            if ("driverId" in sent or "helperIds" in sent) and run_mode(
                    conn.execute("SELECT * FROM bulk_order_runs WHERE id=?", (rid,)).fetchone()) == "truck":
                drv = body.driverId if "driverId" in sent else x["driver_id"]
                helpers = body.helperIds if "helperIds" in sent and body.helperIds is not None else \
                    [h[0] for h in conn.execute("SELECT employee_id FROM run_crew WHERE run_id=?", (rid,))]
                set_crew(conn, rid, drv, helpers)
        r_ = run_dict(conn, conn.execute("SELECT * FROM bulk_order_runs WHERE id=?", (rid,)).fetchone())
        crew_txt = ", ".join(filter(None, [r_["driverName"] and f"driver {r_['driverName']}"] + [h["name"] for h in r_["helpers"]]))
        who = {"pickup": " · the customer picks it up", "other": f" · delivered by {r_['carrier'] or 'someone else'}"}.get(r_["mode"], "")
        note(conn, user, "Delivery plan", f"{x['customer']}: {r_['date'] or 'no date'}{who}{' · ' + crew_txt if crew_txt else ''}"
                                          f"{' · delivered' if r_['done'] else ''}"[:250])
        return r_

    @r.delete("/runs/{rid}")
    def delete_run(rid: int, user=Depends(admin), conn=Depends(get_db)):
        """Planner: remove one delivery run (e.g. an old 'customer picks up' run from before round 17) or a truck trip of
        its own."""
        x = conn.execute("""SELECT r.*, o.customer FROM bulk_order_runs r LEFT JOIN bulk_orders o ON o.id=r.order_id WHERE r.id=?""",
                         (rid,)).fetchone()
        if not x:
            raise HTTPException(404, "Delivery run not found")
        x = {**dict(x), "customer": x["customer"] or trip_label(x)}
        with conn:
            conn.execute("DELETE FROM run_crew WHERE run_id=?", (rid,))
            conn.execute("DELETE FROM bulk_order_runs WHERE id=?", (rid,))
        note(conn, user, "Delivery removed", f"{x['customer']}: {x['date'] or 'no date'}")
        return {"deleted": rid}

    def clean_time(t):
        t = (t or "").strip()
        if not t:
            return None
        m = _re_mod.fullmatch(r"(\d{1,2}):(\d{2})", t)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise HTTPException(422, "time is HH:MM, e.g. 08:00")
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    class TripIn(BaseModel):
        purpose: str = "deliver"          # deliver | get (materials) | personal
        place: str = ""                   # where to go
        note: str = ""                    # what to bring / why
        date: str                         # the day
        time: str = ""                    # leaves at (optional)
        transportId: Optional[int] = None
        driverId: Optional[int] = None
        helperIds: list[int] = []

    @r.post("/trips")
    def add_trip(body: TripIn, user=Depends(admin), conn=Depends(get_db)):
        """Planner -> '+ Truck trip': the truck goes somewhere without an order - to deliver something, to get materials,
        or for personal use. Where, when, which truck, the driver and the helpers; it blocks those people that day
        like a delivery does."""
        if body.purpose not in TRIP_PURPOSES:
            raise HTTPException(422, "purpose is deliver, get or personal")
        day = check_day(body.date)
        tid = body.transportId or (conn.execute("SELECT id FROM transports ORDER BY id LIMIT 1").fetchone() or [None])[0]
        if not tid or not conn.execute("SELECT 1 FROM transports WHERE id=?", (tid,)).fetchone():
            raise HTTPException(409, "There is no truck yet: add one in Sales -> Add Order -> Add delivery")
        with conn:
            rid = conn.execute("""INSERT INTO bulk_order_runs(order_id, transport_id, trips, date, done, mode, purpose, place, note, time)
                                  VALUES(NULL,?,1,?,0,'truck',?,?,?,?)""",
                               (tid, day, body.purpose, body.place.strip()[:160] or None, body.note.strip()[:160] or None,
                                clean_time(body.time))).lastrowid
            set_crew(conn, rid, body.driverId, body.helperIds)
        r_ = run_dict(conn, conn.execute("SELECT * FROM bulk_order_runs WHERE id=?", (rid,)).fetchone())
        note(conn, user, "Truck trip", f"{r_['label']} · {day}{' ' + r_['time'] if r_['time'] else ''}"
                                       + (f" · driver {r_['driverName']}" if r_["driverName"] else ""))
        return r_

    class RunNew(BaseModel):
        mode: str = "truck"               # our truck (round 17: the only way; pickup / other only exist in old data)
        date: str = ""
        transportId: Optional[int] = None
        carrier: str = ""
        carrierCost: float = 0
        trips: int = 1

    @r.post("/orders/{oid}/runs")
    def add_run(oid: int, body: RunNew, user=Depends(admin), conn=Depends(get_db)):
        """Planner -> an order with no delivery plan yet: say who delivers it (our truck / the customer picks it up /
        someone else) and on which day. Add Order's delivery popup only plans our own truck."""
        o = get_order(conn, oid)
        if o["status"] != "active":
            raise HTTPException(409, "This order is already done")
        mode = body.mode if body.mode in RUN_MODES else None
        if mode != "truck":
            raise HTTPException(422, "Deliveries are by our own truck only")
        tid = None
        if mode == "truck":
            tid = body.transportId or (conn.execute("SELECT id FROM transports ORDER BY id LIMIT 1").fetchone() or [None])[0]
            if not tid or not conn.execute("SELECT 1 FROM transports WHERE id=?", (tid,)).fetchone():
                raise HTTPException(409, "There is no truck yet: add one in Sales -> Add Order -> Add delivery")
        with conn:
            rid = conn.execute("""INSERT INTO bulk_order_runs(order_id,transport_id,trips,date,done,mode,carrier,carrier_cost)
                                  VALUES(?,?,?,?,0,?,?,?)""",
                               (oid, tid, max(1, body.trips or 1), check_day(body.date) if body.date else None, mode,
                                (body.carrier or "").strip()[:80] or None if mode != "truck" else None,
                                max(0.0, body.carrierCost or 0) if mode == "other" else None)).lastrowid
        r_ = run_dict(conn, conn.execute("SELECT * FROM bulk_order_runs WHERE id=?", (rid,)).fetchone())
        who = {"pickup": "the customer picks it up", "other": f"delivered by {r_['carrier'] or 'someone else'}"}.get(mode, "our truck")
        note(conn, user, "Delivery plan", f"{o['customer']}: {r_['date'] or 'no date'} · {who}")
        return r_

    def run_status(r_, order_done, t):
        if r_["done"] or order_done:
            return "done"
        if not r_["date"]:
            return "unscheduled"
        return "overdue" if r_["date"] < t else "today" if r_["date"] == t else "upcoming"

    def planner_runs(conn, where, args):
        t = today()
        out = []
        for x in conn.execute(f"""SELECT r.*, o.customer, o.address, o.id oid, t.truck_name, t.plate, t.capacity, t.capacity_value, t.capacity_unit, t.cost
                                  FROM bulk_order_runs r LEFT JOIN bulk_orders o ON o.id=r.order_id
                                  LEFT JOIN transports t ON t.id=r.transport_id
                                  WHERE (o.status='active' OR r.order_id IS NULL) AND {where}
                                  ORDER BY r.date, COALESCE(r.time, ''), COALESCE(o.customer, r.place, '')""", args).fetchall():
            trip = x["order_id"] is None                 # a truck trip of its own (no order, no items)
            items = [] if trip else [{"id": i["id"], "name": i["name"], "qty": i["qty"], "unit": i["unit"] or "", "delivered": bool(i["delivered"])}
                                     for i in conn.execute("SELECT * FROM bulk_order_items WHERE order_id=? ORDER BY id", (x["oid"],))]
            order_done = bool(items) and all(i["delivered"] for i in items)
            if trip:
                x = {**dict(x), "customer": trip_label(x), "address": x["place"] or ""}
            rd = run_dict(conn, x)
            cap = f"{x['capacity_value']:g} {x['capacity_unit']}" if x["capacity_value"] else (x["capacity"] or "")
            name = {"pickup": "Customer picks up", "other": rd["carrier"] or "Someone else"}.get(rd["mode"]) or x["truck_name"] or "No truck"
            out.append({**rd, "orderId": x["oid"], "customer": x["customer"], "address": x["address"] or "", "items": items,
                        "truck": {"id": x["transport_id"] if rd["mode"] == "truck" else None, "name": name,
                                  "plate": (x["plate"] or "") if rd["mode"] == "truck" else "",
                                  "capacity": cap if rd["mode"] == "truck" else "", "cost": (x["cost"] or 0) if rd["mode"] == "truck" else 0},
                        "status": run_status(rd, order_done, t)})
        return out

    @r.get("/planner")
    def planner(start: str, end: str, user=Depends(admin), conn=Depends(get_db)):
        """Planner calendar: per day the deliveries and their crew; plus deliveries with no day yet."""
        check_day(start), check_day(end)
        days = {}
        for run_ in planner_runs(conn, "r.date BETWEEN ? AND ?", (start, end)):
            d = days.setdefault(run_["date"], {"date": run_["date"], "runs": 0, "done": 0, "noDriver": 0, "noHelper": 0,
                                               "overdue": 0, "people": set(), "double": set()})
            d["runs"] += 1
            d["done"] += run_["status"] == "done"
            d["overdue"] += run_["status"] == "overdue"
            if run_["status"] != "done" and run_["mode"] == "truck":     # pick-ups / others need no crew of ours
                d["noDriver"] += not run_["driverId"]
                d["noHelper"] += not run_["helperIds"]
            for pid in [run_["driverId"], *run_["helperIds"]]:
                if pid:
                    (d["double"] if pid in d["people"] else d["people"]).add(pid)
        for d in days.values():
            d["crew"] = len(d.pop("people"))
            d["doubleBooked"] = len(d.pop("double"))
        unscheduled = planner_runs(conn, "(r.date IS NULL OR r.date='')", ())
        no_runs = [{"orderId": o["id"], "customer": o["customer"]} for o in conn.execute(
            """SELECT * FROM bulk_orders o WHERE o.status='active' AND NOT EXISTS (SELECT 1 FROM bulk_order_runs r WHERE r.order_id=o.id)
               AND EXISTS (SELECT 1 FROM bulk_order_items i WHERE i.order_id=o.id AND i.delivered=0) ORDER BY o.id""")]
        upcoming = [x for x in planner_runs(conn, "r.date >= ?", (today(),)) if x["status"] != "done"][:30]
        overdue = [x for x in planner_runs(conn, "r.date < ?", (today(),)) if x["status"] == "overdue"]
        return {"days": sorted(days.values(), key=lambda d: d["date"]), "unscheduled": [x for x in unscheduled if x["status"] != "done"],
                "ordersWithoutRun": no_runs, "upcoming": upcoming, "overdue": overdue,
                "transports": [transport_dict(t_, conn) for t_ in conn.execute("SELECT * FROM transports ORDER BY id")]}

    @r.get("/planner/day/{day}")
    def planner_day(day: str, user=Depends(admin), conn=Depends(get_db)):
        """one day of the Planner: every delivery with its truck, items, driver and helpers + who is still free"""
        d = check_day(day)
        runs = planner_runs(conn, "r.date=?", (d,))
        pool = crew_pool(conn, d)
        open_runs = [x for x in runs if x["status"] != "done"]
        crew_runs = [x for x in open_runs if x["mode"] == "truck"]      # only our trucks need a driver and helpers
        used = {}
        for x in open_runs:
            for pid in [x["driverId"], *x["helperIds"]]:
                if pid:
                    used[pid] = used.get(pid, 0) + 1
        free_helpers = [h for h in pool["helpers"] if h["id"] not in used]
        free_drivers = [h for h in pool["drivers"] if h["id"] not in used]
        free_staff = [h for h in pool["others"] if h["id"] not in used]
        names = {p["id"]: p["name"] for p in pool["drivers"] + pool["helpers"] + pool["others"]}
        for x in open_runs:
            for p_ in [x["driverId"], *x["helperIds"]]:
                if p_ and p_ not in names:
                    names[p_] = (conn.execute("SELECT name FROM employees WHERE id=?", (p_,)).fetchone() or ["?"])[0]
        return {"date": d, "runs": runs, "pool": pool,
                "stats": {"runs": len(runs), "open": len(open_runs), "noDriver": sum(1 for x in crew_runs if not x["driverId"]),
                          "noHelper": sum(1 for x in crew_runs if not x["helperIds"]),
                          "pickups": sum(1 for x in open_runs if x["mode"] == "pickup"),
                          "others": sum(1 for x in open_runs if x["mode"] == "other"),
                          "helpers": len(pool["helpers"]), "helpersFree": len(free_helpers),
                          "drivers": len(pool["drivers"]), "driversFree": len(free_drivers),
                          "staff": len(pool["others"]), "staffFree": len(free_staff),
                          "trucks": len({x["truck"]["id"] for x in crew_runs if x["truck"]["id"]}),
                          "doubleBooked": [names.get(k, "?") for k, v in used.items() if v > 1]},
                "freeHelpers": free_helpers, "freeDrivers": free_drivers, "freeStaff": free_staff,
                "transports": [transport_dict(t_, conn) for t_ in conn.execute("SELECT * FROM transports ORDER BY id")]}

    # ---------------- delivery orders read from the phones' receipts ----------------
    def draft_dict(conn, x):
        data = {}
        try:
            data = _json_mod.loads(x["data"] or "{}")
        except ValueError:
            pass
        rc = conn.execute("SELECT * FROM receipts WHERE id=?", (x["receipt_id"],)).fetchone() if x["receipt_id"] else None
        who = conn.execute("SELECT id, COALESCE(display_name, username) name FROM users WHERE id=?", (x["user_id"],)).fetchone() \
            if x["user_id"] else None
        sold = conn.execute("SELECT COUNT(*) FROM receipt_lines WHERE receipt_id=? AND sold=1", (x["receipt_id"],)).fetchone()[0] \
            if x["receipt_id"] else 0
        photo = None
        if rc:
            photo = UPLOAD_DIR / f"{rc['id']}.jpg"
            if not photo.exists() and rc["image"]:
                photo = Path(rc["image"])
        return {"id": x["id"], "status": x["status"], "created": x["created"], "receiptId": x["receipt_id"],
                "receiptLabel": shop_db.receipt_label(rc["created"]) if rc and rc["created"] else "",
                "receiptStatus": rc["status"] if rc else "", "canReadAgain": bool(rc) and reread is not None and not sold,
                "payment": {"mode": rc["pay_mode"], "why": rc["pay_note"] or "", "sure": bool(rc["pay_sure"])} if rc else None,
                "hasImage": bool(photo and photo.exists()), "person": {"userId": who["id"], "name": who["name"]} if who else None,
                "customer": x["customer"] or "", "address": x["address"] or "", "date": x["delivery_date"] or "",
                "rows": data.get("rows", []), "unmatched": data.get("unmatched", []), "words": data.get("words", []),
                "alsoSold": sold > 0, "orderId": x["order_id"]}

    def get_draft(conn, did):
        x = conn.execute("SELECT * FROM delivery_drafts WHERE id=?", (did,)).fetchone()
        if not x:
            raise HTTPException(404, "That delivery order is gone")
        return x

    @r.get("/delivery-drafts")
    def delivery_drafts(status: str = "new", userId: Optional[int] = None, user=Depends(admin), conn=Depends(get_db)):
        rows = conn.execute("""SELECT * FROM delivery_drafts WHERE (?='all' OR status=?) AND (? IS NULL OR user_id=?)
                               ORDER BY id DESC LIMIT 200""", (status, status, userId, userId)).fetchall()
        return [draft_dict(conn, x) for x in rows]

    @r.get("/delivery-drafts/{did}")
    def delivery_draft(did: int, user=Depends(admin), conn=Depends(get_db)):
        return draft_dict(conn, get_draft(conn, did))

    @r.post("/delivery-drafts/{did}/dismiss")
    def dismiss_draft(did: int, user=Depends(admin), conn=Depends(get_db)):
        x = get_draft(conn, did)
        with conn:
            conn.execute("UPDATE delivery_drafts SET status='dismissed', handled=? WHERE id=? AND status='new'", (now(), did))
        note(conn, user, "Delivery order dismissed", f"receipt #{x['receipt_id']} ({x['customer'] or 'no customer'})")
        return draft_dict(conn, get_draft(conn, did))

    @r.post("/delivery-drafts/{did}/apply")
    def apply_draft(did: int, body: OrderIn, user=Depends(admin), conn=Depends(get_db)):
        """the owner checked / edited the filled-in order and pressed Apply: it becomes a normal Add Order order"""
        x = get_draft(conn, did)
        if x["status"] != "new":
            raise HTTPException(409, "This delivery order was already applied or dismissed")

        def mark(c, oid):
            n = c.execute("UPDATE delivery_drafts SET status='done', order_id=?, handled=? WHERE id=? AND status='new'",
                          (oid, now(), did)).rowcount
            if not n:                                   # applied twice at the same moment: undo the second order
                raise HTTPException(409, "This delivery order was just applied")
        # the phone's Update of that receipt sells it and takes the stock: the order does not take it a second time
        return create_order(conn, body, user, extra=mark, take=False)

    # ---------------- payments (Payments page) ----------------
    # Every payment is CASH or BANK, nothing more: no bank account is linked or needed (the shop uses one bank).
    #   sales (phones):   read from the receipt photo (CASH SALES ticked = cash; a PAID / PAID IN BANK stamp or a bank
    #                     word = bank; nothing = cash). The owner can switch one sale in Payments by day.
    #   order payments:   Cash or Bank is picked when the payment is recorded (Sales -> order, or Payments).
    # Nothing here connects to a bank: no card numbers, CVV, PIN or passwords are accepted.
    # (pay_accounts / the sales bank are kept only so data from before still opens; the page no longer shows them.)
    def sales_bank(conn):
        v = shop_db.get_setting(conn, "sales_bank_account")
        a = conn.execute("SELECT * FROM pay_accounts WHERE id=?", (int(v),)).fetchone() if v and str(v).isdigit() else None
        return a or conn.execute("SELECT * FROM pay_accounts ORDER BY id LIMIT 1").fetchone()

    def bank_name(a):
        return f"{a['bank']}{' · ' + a['nickname'] if a['nickname'] else ''}" if a else "Bank"

    def bank_sales_of(conn, a, sb):
        """(total, count) of the bank sales that went into account a: only the sales bank gets them, from its opening date"""
        if not a or not sb or a["id"] != sb["id"]:
            return 0.0, 0
        x = conn.execute("""SELECT COALESCE(SUM(total),0), COUNT(*) FROM sales WHERE pay_mode='bank'
                            AND substr(created,1,10) >= ?""", (a["opening_date"] or "0000",)).fetchone()
        return float(x[0] or 0), x[1]

    def method_of(x):
        """'cash' | 'bank' of a pay_entries row (a line from before: no account = cash, an account = bank)"""
        m = x["method"]
        return m if m in ("cash", "bank") else ("bank" if x["account_id"] else "cash")

    METHOD_LABEL = {"cash": "Cash", "bank": "Bank"}

    class PayAccountIn(BaseModel):
        bank: str
        nickname: str = ""
        holder: str = ""
        last4: str = ""
        opening: float = 0
        openingDate: str = ""

    class PayEntryIn(BaseModel):
        accountId: Optional[int] = None          # old: an account = bank
        method: Optional[str] = None             # cash | bank
        direction: str = "in"
        amount: float
        date: str = ""
        party: str = ""
        ref: str = ""
        note: str = ""
        orderId: Optional[int] = None

    def no_secrets(*texts, digits=8):
        """refuse text that holds a long number (a card / account number). Payments allow a phone number (11 digits)."""
        import re
        for t in texts:
            if t and any(len(re.sub(r"\D", "", m)) >= digits for m in re.findall(r"\d[\d\s-]*\d", t)):
                raise HTTPException(422, "Do not type card / account numbers here. Only the last 4 digits are kept.")

    def order_pays(conn, oid):
        """the payments of one order, each with how it came in: cash | bank | None (typed in Sales before, not said)"""
        out = []
        for x in conn.execute("""SELECT p.*, e.id eid, e.account_id, e.method, e.ref, e.photo, e.created ecreated
                                 FROM bulk_order_payments p LEFT JOIN pay_entries e ON e.order_payment_id=p.id
                                 WHERE p.order_id=? ORDER BY p.date, p.id""", (oid,)):
            m = method_of(x) if x["eid"] else None
            out.append({"id": x["id"], "entryId": x["eid"], "date": x["date"], "amount": x["amount"], "note": x["note"] or "",
                        "method": m, "where": METHOD_LABEL[m] if m else "Recorded in Sales", "ref": x["ref"] or "",
                        "hasPhoto": bool(x["photo"]), "time": (x["ecreated"] or "")[11:16]})
        return out

    def order_state(conn, o):
        total = order_money(conn, o)["total"]
        pays = order_pays(conn, o["id"])
        paid = sum(x["amount"] for x in pays)
        items = conn.execute("SELECT COUNT(*), COALESCE(SUM(delivered),0) FROM bulk_order_items WHERE order_id=?", (o["id"],)).fetchone()
        st = "complete" if total > 0 and paid >= total - 0.005 else "incomplete" if paid > 0 else "missing"
        last = max((x["date"] for x in pays), default=None)
        return {"id": o["id"], "customer": o["customer"], "date": (o["created"] or "")[:10], "orderStatus": o["status"],
                "total": round(total, 2), "paid": round(paid, 2), "balance": round(max(0, total - paid), 2), "state": st,
                "delivered": f"{items[1]}/{items[0]}", "payments": pays, "lastPaid": last}

    def entry_dict(x, groups=None):
        m = method_of(x)
        return {"id": x["id"], "accountId": x["account_id"], "method": m, "direction": x["direction"], "amount": x["amount"],
                "date": x["date"], "party": x["party"] or "", "ref": x["ref"] or "", "note": x["note"] or "",
                "orderId": x["order_id"], "hasPhoto": bool(x["photo"])}

    def account_dict(conn, a, sb=None):
        ins, outs, n = conn.execute("""SELECT COALESCE(SUM(CASE WHEN direction='in' THEN amount END),0),
                                              COALESCE(SUM(CASE WHEN direction='out' THEN amount END),0), COUNT(*)
                                       FROM pay_entries WHERE account_id=?""", (a["id"],)).fetchone()
        sb = sb if sb is not None else sales_bank(conn)
        s_in, s_n = bank_sales_of(conn, a, sb)
        return {"id": a["id"], "bank": a["bank"], "nickname": a["nickname"] or "", "holder": a["holder"] or "",
                "last4": a["last4"] or "", "opening": a["opening"], "openingDate": a["opening_date"] or "",
                "in": round(ins + s_in, 2), "out": outs, "count": n + s_n, "salesIn": round(s_in, 2), "salesCount": s_n,
                "isSalesBank": bool(sb and sb["id"] == a["id"]),
                "balance": round((a["opening"] or 0) + ins + s_in - outs, 2)}

    def payment_rows(conn, month=None):
        """Every payment that came in, as short lines (name, date, amount) for the Cash / Bank lists:
        the sales of one sales person on one day = ONE line; one order payment = one line; other money in = one line."""
        rows = []
        like = f"{month}%" if month else "%"
        for x in conn.execute("""SELECT substr(s.created,1,10) day, COALESCE(u.display_name, u.username, 'Desktop') person,
                                        CASE WHEN s.pay_mode='bank' THEN 'bank' ELSE 'cash' END m,
                                        SUM(s.total) total, COUNT(*) n, MAX(s.created) last
                                 FROM sales s LEFT JOIN users u ON u.id=s.user_id
                                 WHERE s.created LIKE ? GROUP BY day, person, m""", (like,)):
            rows.append({"key": f"s{x['day']}{x['person']}{x['m']}", "kind": "sales", "method": x["m"], "date": x["day"],
                         "name": x["person"], "amount": round(x["total"] or 0, 2), "count": x["n"],
                         "sub": f"{x['n']} sale{'s' if x['n'] != 1 else ''}", "at": x["last"] or x["day"]})
        for x in conn.execute("""SELECT p.*, o.customer, e.id eid, e.account_id, e.method, e.created ecreated
                                 FROM bulk_order_payments p JOIN bulk_orders o ON o.id=p.order_id
                                 LEFT JOIN pay_entries e ON e.order_payment_id=p.id
                                 WHERE p.date LIKE ?""", (like,)):
            m = method_of(x) if x["eid"] else "cash"
            rows.append({"key": f"o{x['id']}", "kind": "order", "method": m, "date": x["date"], "name": x["customer"],
                         "amount": round(x["amount"] or 0, 2), "count": 1, "orderId": x["order_id"], "paymentId": x["id"],
                         "notSaid": not x["eid"], "sub": "order payment" + (" · cash or bank not said" if not x["eid"] else ""),
                         "at": (x["ecreated"] or x["date"]).replace(" ", "T")})
        for x in conn.execute("""SELECT * FROM pay_entries WHERE order_payment_id IS NULL AND direction='in' AND date LIKE ?""", (like,)):
            rows.append({"key": f"e{x['id']}", "kind": "other", "method": method_of(x), "date": x["date"],
                         "name": x["party"] or x["note"] or "Money in", "amount": round(x["amount"] or 0, 2), "count": 1,
                         "entryId": x["id"], "sub": x["note"] if x["party"] and x["note"] else "", "at": (x["created"] or x["date"]).replace(" ", "T")})
        rows.sort(key=lambda r_: (r_["date"], r_["at"], r_["key"]))
        return rows

    def totals(rows):
        out = {}
        for m in ("cash", "bank"):
            mine = [x for x in rows if x["method"] == m]
            out[m] = {"count": sum(x["count"] for x in mine), "total": round(sum(x["amount"] for x in mine), 2)}
        return out

    @r.get("/payments")
    def payments_overview(user=Depends(admin), conn=Depends(get_db)):
        orders = [order_state(conn, o) for o in conn.execute("SELECT * FROM bulk_orders ORDER BY id DESC")]
        orders = [o for o in orders if o["total"] > 0]
        tot = totals(payment_rows(conn))
        month = today()[:7]
        tm = totals(payment_rows(conn, month))
        return {"cash": tot["cash"], "bank": tot["bank"], "month": month, "cashMonth": tm["cash"], "bankMonth": tm["bank"],
                "orders": orders, "owed": round(sum(o["balance"] for o in orders), 2),
                "banks": []}                                   # no bank accounts any more (kept for older screens)

    @r.get("/payments/list")
    def payments_list(kind: str = "cash", month: Optional[str] = None, user=Depends(admin), conn=Depends(get_db)):
        """Payments page -> Cash payments / Bank payments: one short line per payment (name, date, amount), newest first,
        with the running total ('so far'). month = YYYY-MM, or nothing for all."""
        if kind not in ("cash", "bank"):
            raise HTTPException(422, "kind is cash or bank")
        if month and not _re_mod.fullmatch(r"\d{4}-\d{2}", month):
            raise HTTPException(422, "month is YYYY-MM")
        rows = [x for x in payment_rows(conn, month) if x["method"] == kind]
        run = 0.0
        for x in rows:
            run += x["amount"]
            x["soFar"] = round(run, 2)
        all_rows = [x for x in payment_rows(conn) if x["method"] == kind] if month else rows
        months = sorted({x["date"][:7] for x in all_rows}, reverse=True)
        return {"kind": kind, "month": month or "", "rows": list(reversed(rows)), "total": round(run, 2),
                "count": sum(x["count"] for x in rows), "allTotal": round(sum(x["amount"] for x in all_rows), 2),
                "months": months}

    @r.post("/payments/accounts/{aid}/sales-bank")
    def set_sales_bank(aid: int, user=Depends(admin), conn=Depends(get_db)):
        """(old screens) 'Use for bank sales'."""
        a = conn.execute("SELECT * FROM pay_accounts WHERE id=?", (aid,)).fetchone()
        if not a:
            raise HTTPException(404, "Account not found")
        with conn:
            shop_db.set_setting(conn, "sales_bank_account", str(aid))
        note(conn, user, "Bank for sales", bank_name(a))
        return account_dict(conn, a)

    class SalePayIn(BaseModel):
        mode: str                                        # cash | bank

    @r.put("/sales/{sid}/payment")
    def set_sale_payment(sid: int, body: SalePayIn, user=Depends(admin), conn=Depends(get_db)):
        """Switch how one sale was paid (the reader got it wrong): cash <-> bank."""
        if body.mode not in ("cash", "bank"):
            raise HTTPException(422, "mode is cash or bank")
        s_ = conn.execute("SELECT * FROM sales WHERE id=?", (sid,)).fetchone()
        if not s_:
            raise HTTPException(404, "Sale not found")
        who = user["display_name"] or user["username"]
        with conn:
            conn.execute("UPDATE sales SET pay_mode=?, pay_note=? WHERE id=?", (body.mode, f"set by {who}", sid))
        note(conn, user, "Sale payment", f"sale #{sid} {peso_txt(s_['total'])}: {body.mode}")
        return {"id": sid, "payMode": body.mode, "why": f"set by {who}"}

    @r.get("/payments/day")
    def payments_day(date: Optional[str] = None, user=Depends(admin), conn=Depends(get_db)):
        """Payments page -> one day: the order payments (Sales -> Add Order) first, then each sales person's sales, each
        with how it was paid (Cash or Bank) and the time. Plus other money in / out recorded that day."""
        d = check_day(date) if date else today()
        hm_ = lambda x: (x or "")[11:16]
        orders = {}
        for x in conn.execute("""SELECT p.*, o.customer, e.id eid, e.created ecreated, e.ref, e.photo, e.account_id, e.method
                                 FROM bulk_order_payments p JOIN bulk_orders o ON o.id=p.order_id
                                 LEFT JOIN pay_entries e ON e.order_payment_id=p.id
                                 WHERE p.date=? ORDER BY p.id""", (d,)):
            if x["eid"]:
                kind = method_of(x)
                method = METHOD_LABEL[kind]
            else:
                method, kind = "Recorded in Sales", "unknown"
            o = orders.setdefault(x["order_id"], {"orderId": x["order_id"], "customer": x["customer"], "payments": [], "total": 0.0})
            o["payments"].append({"id": x["id"], "entryId": x["eid"], "amount": x["amount"], "method": method, "kind": kind,
                                  "time": hm_(x["ecreated"]), "ref": x["ref"] or "", "note": x["note"] or "",
                                  "hasPhoto": bool(x["photo"])})
            o["total"] += x["amount"]
        st = {o["id"]: order_state(conn, o) for o in conn.execute(
            f"SELECT * FROM bulk_orders WHERE id IN ({','.join('?' * len(orders))})", tuple(orders))} if orders else {}
        for oid, o in orders.items():
            o.update(orderTotal=st[oid]["total"], orderPaid=st[oid]["paid"], state=st[oid]["state"])
        people = {}
        for x in conn.execute("""SELECT s.id, s.created, s.total, s.user_id, s.pay_mode, s.pay_note,
                                        COALESCE(u.display_name, u.username, 'Desktop') person
                                 FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE substr(s.created,1,10)=? ORDER BY s.created""", (d,)):
            p_ = people.setdefault(x["person"], {"person": x["person"], "userId": x["user_id"], "sales": [], "total": 0.0})
            lines = [{"item": it["name"] or "(deleted item)", "unit": it["unit"] or "", "qty": it["qty"], "price": it["sell_price"],
                      "amount": round((it["qty"] or 0) * (it["sell_price"] or 0), 2)}
                     for it in conn.execute("""SELECT si.*, i.name, i.unit FROM sale_items si LEFT JOIN items i ON i.id=si.item_id
                                               WHERE si.sale_id=? ORDER BY si.id""", (x["id"],))]
            bank = x["pay_mode"] == "bank"
            p_["sales"].append({"id": x["id"], "time": hm_(x["created"].replace(" ", "T")), "total": x["total"] or 0, "items": len(lines),
                                "lines": lines, "method": "Bank" if bank else "Cash", "kind": "bank" if bank else "cash",
                                "payMode": "bank" if bank else "cash",
                                "why": x["pay_note"] or ("made before payment modes were read: counted as cash" if not x["pay_mode"] else "")})
            p_["total"] += x["total"] or 0
        other = [{"id": x["id"], "direction": x["direction"], "amount": x["amount"], "party": x["party"] or "", "note": x["note"] or "",
                  "ref": x["ref"] or "", "time": hm_(x["created"]), "method": METHOD_LABEL[method_of(x)]}
                 for x in conn.execute("""SELECT e.* FROM pay_entries e WHERE e.date=? AND e.order_payment_id IS NULL ORDER BY e.id""", (d,))]
        by = {}
        for o in orders.values():
            for p_ in o["payments"]:
                by[p_["method"]] = by.get(p_["method"], 0) + p_["amount"]
        for p_ in people.values():
            for s_ in p_["sales"]:
                by[s_["method"]] = by.get(s_["method"], 0) + s_["total"]
        return {"date": d, "orders": list(orders.values()), "people": sorted(people.values(), key=lambda p: -p["total"]),
                "other": other, "byMethod": [{"method": k, "amount": round(v, 2)} for k, v in sorted(by.items(), key=lambda kv: -kv[1])],
                "total": round(sum(by.values()), 2)}

    @r.post("/payments/accounts")
    def add_pay_account(body: PayAccountIn, user=Depends(admin), conn=Depends(get_db)):
        bank = body.bank.strip()
        last4 = "".join(ch for ch in body.last4 if ch.isdigit())
        if not bank:
            raise HTTPException(422, "Type the bank's name")
        if len(last4) > 4:
            raise HTTPException(422, "Only the LAST 4 digits, never the whole card or account number")
        no_secrets(body.nickname, body.holder, bank)
        with conn:
            aid = conn.execute("INSERT INTO pay_accounts(bank,nickname,holder,last4,opening,opening_date,created) VALUES(?,?,?,?,?,?,?)",
                               (bank, body.nickname.strip(), body.holder.strip(), last4, body.opening or 0,
                                clean(body.openingDate) or today(), now())).lastrowid
        note(conn, user, "Bank account", f"added {bank}{' · ' + body.nickname.strip() if body.nickname.strip() else ''}")
        return account_dict(conn, conn.execute("SELECT * FROM pay_accounts WHERE id=?", (aid,)).fetchone())

    @r.put("/payments/accounts/{aid}")
    def edit_pay_account(aid: int, body: PayAccountIn, user=Depends(admin), conn=Depends(get_db)):
        if not conn.execute("SELECT 1 FROM pay_accounts WHERE id=?", (aid,)).fetchone():
            raise HTTPException(404, "Account not found")
        last4 = "".join(ch for ch in body.last4 if ch.isdigit())
        if len(last4) > 4:
            raise HTTPException(422, "Only the LAST 4 digits, never the whole card or account number")
        no_secrets(body.nickname, body.holder, body.bank)
        with conn:
            conn.execute("UPDATE pay_accounts SET bank=?, nickname=?, holder=?, last4=?, opening=?, opening_date=? WHERE id=?",
                         (body.bank.strip(), body.nickname.strip(), body.holder.strip(), last4, body.opening or 0,
                          clean(body.openingDate) or today(), aid))
        return account_dict(conn, conn.execute("SELECT * FROM pay_accounts WHERE id=?", (aid,)).fetchone())

    @r.delete("/payments/accounts/{aid}")
    def delete_pay_account(aid: int, user=Depends(admin), conn=Depends(get_db)):
        """Removes the account and its passbook lines. Order payments stay paid in Sales (they become 'Recorded in Sales')."""
        a = conn.execute("SELECT * FROM pay_accounts WHERE id=?", (aid,)).fetchone()
        if not a:
            raise HTTPException(404, "Account not found")
        photos = [x[0] for x in conn.execute("SELECT photo FROM pay_entries WHERE account_id=? AND photo IS NOT NULL", (aid,))]
        with conn:
            conn.execute("DELETE FROM pay_entries WHERE account_id=?", (aid,))
            conn.execute("DELETE FROM pay_accounts WHERE id=?", (aid,))
            if shop_db.get_setting(conn, "sales_bank_account") == str(aid):
                conn.execute("DELETE FROM settings WHERE key='sales_bank_account'")
        for f in photos:
            (PAY_DIR / f).unlink(missing_ok=True)
        note(conn, user, "Bank account", f"removed {a['bank']}{' · ' + a['nickname'] if a['nickname'] else ''}")
        return {"deleted": aid}

    @r.get("/payments/passbook")
    def passbook(account: Optional[int] = None, user=Depends(admin), conn=Depends(get_db)):
        """account = an account id, or nothing for CASH. Payments for the same order are ONE line (click it to see
        each payment); the balance runs like a bank passbook."""
        a = None
        if account is not None:
            a = conn.execute("SELECT * FROM pay_accounts WHERE id=?", (account,)).fetchone()
            if not a:
                raise HTTPException(404, "Account not found")
            rows = conn.execute("SELECT * FROM pay_entries WHERE account_id=? ORDER BY date, id", (account,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pay_entries WHERE account_id IS NULL ORDER BY date, id").fetchall()
        customers = {o["id"]: o for o in conn.execute("SELECT * FROM bulk_orders")}
        lines, by_order = [], {}
        for x in rows:
            e = entry_dict(x)
            if x["order_id"] and x["order_id"] in customers and x["direction"] == "in":
                g = by_order.get(x["order_id"])
                if g is None:
                    st = order_state(conn, customers[x["order_id"]])
                    g = by_order[x["order_id"]] = {"kind": "order", "orderId": x["order_id"], "party": st["customer"],
                                                   "orderTotal": st["total"], "orderPaid": st["paid"], "state": st["state"],
                                                   "entries": []}
                    lines.append(g)
                g["entries"].append(e)
            else:
                lines.append({"kind": "entry", **e})
        for g in lines:                          # an order line sits at the date of its latest payment
            if g["kind"] == "order":
                g["date"] = max(e["date"] for e in g["entries"])
                g["amount"] = round(sum(e["amount"] for e in g["entries"]), 2)
                g["direction"] = "in"
        sb = sales_bank(conn) if a else None
        if a and sb and sb["id"] == a["id"]:             # sales paid by bank / e-wallet land here
            groups = {}
            for x in conn.execute("""SELECT s.id, s.created, s.total, s.pay_note, COALESCE(u.display_name, u.username, 'Desktop') person
                                     FROM sales s LEFT JOIN users u ON u.id=s.user_id
                                     WHERE s.pay_mode='bank' AND substr(s.created,1,10) >= ? ORDER BY s.created, s.id""",
                                  (a["opening_date"] or "0000",)):
                day_ = x["created"][:10]
                g = groups.get((day_, x["person"]))
                if g is None:
                    g = groups[(day_, x["person"])] = {"kind": "sales", "date": day_, "party": x["person"], "direction": "in",
                                                         "amount": 0.0, "sales": [], "key": f"s{day_}{x['person']}"}
                    lines.append(g)
                g["sales"].append({"id": x["id"], "time": (x["created"] or "")[11:16], "total": x["total"] or 0, "why": x["pay_note"] or ""})
                g["amount"] = round(g["amount"] + (x["total"] or 0), 2)
        lines.sort(key=lambda g: (g["date"], 10 ** 9 if g["kind"] == "sales" else
                                  (g.get("id") or max(e["id"] for e in g.get("entries", [{"id": 0}])))))
        bal = (a["opening"] or 0) if a else 0
        for g in lines:
            bal += g["amount"] if g["direction"] == "in" else -g["amount"]
            g["balance"] = round(bal, 2)
        return {"account": account_dict(conn, a) if a else None, "opening": (a["opening"] or 0) if a else 0,
                "openingDate": (a["opening_date"] if a else "") or "", "lines": lines, "balance": round(bal, 2)}

    def record_order_payment(conn, o, amount, d, method, note_="", ref="", party=""):
        """ONE payment for an order: the order's payment row (Sales shows it) + its Payments line (cash or bank).
        Runs inside the caller's transaction. -> pay_entries id"""
        method = method if method in ("cash", "bank") else "cash"
        label = METHOD_LABEL[method]
        opid = conn.execute("INSERT INTO bulk_order_payments(order_id,amount,note,date) VALUES(?,?,?,?)",
                            (o["id"], amount, " ".join(x for x in (label, (ref or "").strip(), (note_ or "").strip()) if x), d)).lastrowid
        return conn.execute("""INSERT INTO pay_entries(account_id,method,direction,amount,date,party,ref,note,order_id,order_payment_id,created)
                               VALUES(NULL,?,'in',?,?,?,?,?,?,?,?)""",
                            (method, amount, d, (party or "").strip() or o["customer"], (ref or "").strip(), (note_ or "").strip(),
                             o["id"], opid, now())).lastrowid

    @r.post("/payments/entries")
    def add_pay_entry(body: PayEntryIn, user=Depends(admin), conn=Depends(get_db)):
        if body.direction not in ("in", "out") or not body.amount or body.amount <= 0:
            raise HTTPException(422, "Amount must be above 0 (money in or out)")
        no_secrets(body.party, body.note, digits=15)
        if body.accountId is not None and not conn.execute("SELECT 1 FROM pay_accounts WHERE id=?", (body.accountId,)).fetchone():
            raise HTTPException(404, "Account not found")
        method = "bank" if body.accountId is not None else (body.method or "cash")
        if method not in ("cash", "bank"):
            raise HTTPException(422, "method is cash or bank")
        o = get_order(conn, body.orderId) if body.orderId else None
        if o and body.direction != "in":
            raise HTTPException(422, "An order payment is money IN")
        d = clean(body.date) or today()
        with conn:
            if o:      # Sales sees the payment exactly like one typed there
                eid = record_order_payment(conn, o, body.amount, d, method, body.note, body.ref, body.party)
            else:
                eid = conn.execute("""INSERT INTO pay_entries(account_id,method,direction,amount,date,party,ref,note,created)
                                      VALUES(?,?,?,?,?,?,?,?,?)""",
                                   (body.accountId, method, body.direction, body.amount, d, body.party.strip(), body.ref.strip(),
                                    body.note.strip(), now())).lastrowid
        note(conn, user, "Payment " + body.direction, f"{METHOD_LABEL[method]}: {peso_txt(body.amount)}" + (f" - {o['customer']}" if o else ""))
        return entry_dict(conn.execute("SELECT * FROM pay_entries WHERE id=?", (eid,)).fetchone())

    @r.post("/payments/link")
    def link_sales_payment(paymentId: int = Query(...), method: Optional[str] = Query(None), accountId: Optional[int] = Query(None),
                           user=Depends(admin), conn=Depends(get_db)):
        """A payment typed in Sales before cash / bank was asked: say how it came in (cash or bank)."""
        p = conn.execute("SELECT * FROM bulk_order_payments WHERE id=?", (paymentId,)).fetchone()
        if not p:
            raise HTTPException(404, "Payment not found")
        if conn.execute("SELECT 1 FROM pay_entries WHERE order_payment_id=?", (paymentId,)).fetchone():
            raise HTTPException(409, "That payment already says cash or bank")
        if accountId is not None and not conn.execute("SELECT 1 FROM pay_accounts WHERE id=?", (accountId,)).fetchone():
            raise HTTPException(404, "Account not found")
        m = "bank" if accountId is not None else (method or "cash")
        if m not in ("cash", "bank"):
            raise HTTPException(422, "method is cash or bank")
        o = get_order(conn, p["order_id"])
        with conn:
            eid = conn.execute("""INSERT INTO pay_entries(account_id,method,direction,amount,date,party,note,order_id,order_payment_id,created)
                                  VALUES(?,?,'in',?,?,?,?,?,?,?)""",
                               (accountId, m, p["amount"], p["date"], o["customer"], p["note"] or "", o["id"], p["id"], now())).lastrowid
        note(conn, user, "Order payment", f"{o['customer']}: {peso_txt(p['amount'])} = {METHOD_LABEL[m]}")
        return entry_dict(conn.execute("SELECT * FROM pay_entries WHERE id=?", (eid,)).fetchone())

    @r.put("/payments/entries/{eid}/method")
    def set_entry_method(eid: int, body: SalePayIn, user=Depends(admin), conn=Depends(get_db)):
        """Switch one payment between cash and bank."""
        if body.mode not in ("cash", "bank"):
            raise HTTPException(422, "mode is cash or bank")
        x = conn.execute("SELECT * FROM pay_entries WHERE id=?", (eid,)).fetchone()
        if not x:
            raise HTTPException(404, "Payment not found")
        with conn:
            conn.execute("UPDATE pay_entries SET method=? WHERE id=?", (body.mode, eid))
            if x["order_payment_id"]:          # the note the Sales page shows starts with Cash / Bank
                p = conn.execute("SELECT note FROM bulk_order_payments WHERE id=?", (x["order_payment_id"],)).fetchone()
                if p:
                    rest = _re_mod.sub(r"^(cash|bank)\b\s*", "", p["note"] or "", flags=_re_mod.I)
                    conn.execute("UPDATE bulk_order_payments SET note=? WHERE id=?",
                                 (" ".join(t for t in (METHOD_LABEL[body.mode], rest) if t), x["order_payment_id"]))
        note(conn, user, "Payment", f"{peso_txt(x['amount'])}: {METHOD_LABEL[body.mode]}")
        return entry_dict(conn.execute("SELECT * FROM pay_entries WHERE id=?", (eid,)).fetchone())

    @r.delete("/payments/entries/{eid}")
    def delete_pay_entry(eid: int, user=Depends(admin), conn=Depends(get_db)):
        """Also removes the order payment it made (the order in Sales goes back to owing that amount)."""
        x = conn.execute("SELECT * FROM pay_entries WHERE id=?", (eid,)).fetchone()
        if not x:
            raise HTTPException(404, "Entry not found")
        with conn:
            if x["order_payment_id"]:
                conn.execute("DELETE FROM bulk_order_payments WHERE id=?", (x["order_payment_id"],))
            conn.execute("DELETE FROM pay_entries WHERE id=?", (eid,))
        if x["photo"]:
            (PAY_DIR / x["photo"]).unlink(missing_ok=True)
        note(conn, user, "Payment removed", peso_txt(x["amount"]))
        return {"deleted": eid}

    @r.post("/payments/entries/{eid}/photo")
    def pay_photo_upload(eid: int, image: UploadFile = File(...), user=Depends(admin), conn=Depends(get_db)):
        if not conn.execute("SELECT 1 FROM pay_entries WHERE id=?", (eid,)).fetchone():
            raise HTTPException(404, "Entry not found")
        save_jpeg(image, PAY_DIR / f"{eid}.jpg", (1600, 1600))
        with conn:
            conn.execute("UPDATE pay_entries SET photo=? WHERE id=?", (f"{eid}.jpg", eid))
        return entry_dict(conn.execute("SELECT * FROM pay_entries WHERE id=?", (eid,)).fetchone())

    @r.get("/payments/entries/{eid}/photo")
    def pay_photo(eid: int, user=Depends(admin_q), conn=Depends(get_db)):
        x = conn.execute("SELECT photo FROM pay_entries WHERE id=?", (eid,)).fetchone()
        if not x or not x["photo"] or not (PAY_DIR / x["photo"]).exists():
            raise HTTPException(404, "No photo")
        return FileResponse(PAY_DIR / x["photo"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    class TransportIn(BaseModel):
        truckName: str
        plate: str = ""
        capacityValue: Optional[float] = None    # 4
        capacityUnit: str = "metric tons"        # from the units list
        capacity: str = ""                       # old free-text form ("4 mt"), still accepted
        cost: float = 0

    def transport_values(body):
        v, u = body.capacityValue, shop_db.unit_name(body.capacityUnit)
        if v is None and body.capacity.strip():
            v, u = shop_db.split_capacity(body.capacity)
        return v, u, (f"{v:g} {u}" if v else "")

    @r.post("/transports")
    def add_transport(body: TransportIn, user=Depends(admin), conn=Depends(get_db)):
        if not body.truckName.strip():
            raise HTTPException(422, "Truck name is required")
        v, u, text = transport_values(body)
        with conn:
            tid = conn.execute("""INSERT INTO transports(truck_name,plate,capacity,capacity_value,capacity_unit,cost,created)
                                  VALUES(?,?,?,?,?,?,?)""",
                               (body.truckName.strip(), body.plate.strip(), text, v, u, body.cost, now())).lastrowid
        note(conn, user, "New truck", f"{body.truckName.strip()} {body.plate.strip()} ({text})")
        return transport_dict(conn.execute("SELECT * FROM transports WHERE id=?", (tid,)).fetchone(), conn)

    @r.put("/transports/{tid}")
    def edit_transport(tid: int, body: TransportIn, user=Depends(admin), conn=Depends(get_db)):
        if not conn.execute("SELECT 1 FROM transports WHERE id=?", (tid,)).fetchone():
            raise HTTPException(404, "Truck not found")
        v, u, text = transport_values(body)
        with conn:
            conn.execute("UPDATE transports SET truck_name=?, plate=?, capacity=?, capacity_value=?, capacity_unit=?, cost=? WHERE id=?",
                         (body.truckName.strip(), body.plate.strip(), text, v, u, body.cost, tid))
        return transport_dict(conn.execute("SELECT * FROM transports WHERE id=?", (tid,)).fetchone(), conn)

    # ---------------- dashboard priority cards (computed from the data) ----------------
    def add_period(d, v, unit):
        v = max(1, int(v or 1))
        if unit == "months":
            m = d.month - 1 + v
            y, m = d.year + m // 12, m % 12 + 1
            day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
            return d.replace(year=y, month=m, day=day)
        return d + timedelta(days=v * (7 if unit == "weeks" else 1))

    def cycle_days(v, unit):
        v = max(1, int(v or 1))
        return v * {"months": 30.44, "weeks": 7}.get(unit, 1)

    def is_long(v, unit):
        """paid less often than once a month (change oil every 6 months, a yearly permit)"""
        return cycle_days(v, unit) > 31.5

    def months_in_cycle(v, unit):
        return max(1, int(v or 1)) if unit == "months" else round(cycle_days(v, unit) / 30.44, 1)

    def monthly_share(amount, v, unit):
        """P2,500 every 6 months -> P416.67 a month"""
        return (amount or 0) / max(1, int(v or 1)) if unit == "months" else (amount or 0) * 30.44 / cycle_days(v, unit)

    def add_period_back(d, v, unit):
        v = max(1, int(v or 1))
        if unit == "months":
            m = d.month - 1 - v
            y, m = d.year + m // 12, m % 12 + 1
            day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
            return d.replace(year=y, month=m, day=day)
        return d - timedelta(days=v * (7 if unit == "weeks" else 1))

    def obl_periods(o, st):
        """how many payments a loan / bill has: from start to end date; None = no end date (a bill that keeps coming,
        e.g. tire change P2,300 every 6 months) -> every payment is the full final_payment amount"""
        en = parse_day(o["end_date"])
        if not (en and st and en > st):
            return None if o["kind"] == "payment" else 1         # a loan without an end date: one payment
        step = (add_period(st, o["freq_value"], o["freq_unit"]) - st).days or 1
        return max(1, round((en - st).days / step))

    def parse_day(x):
        try:
            return datetime.strptime(str(x)[:10], "%Y-%m-%d")
        except (TypeError, ValueError):
            return None

    def every(v, unit):
        return unit[:-1] if int(v or 1) == 1 else f"{v} {unit}"     # "month", "2 weeks"

    def money(n):
        return f"₱{round(n or 0):,}"

    def obligation_due(conn, o):
        """(next due date, amount per payment) of a loan / recurring payment, or (None, 0) when fully paid."""
        start = parse_day(o["start_date"])
        if not start:
            return None, 0
        paid_n, paid = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM obligation_payments WHERE obligation_id=?",
                                    (o["id"],)).fetchone()
        periods = obl_periods(o, start)                 # None = a bill with no end date: never "fully paid"
        if periods is not None and paid >= o["final_payment"] > 0:
            return None, 0
        due = start if paid_n == 0 else add_period(start, o["freq_value"] * paid_n, "months") if o["freq_unit"] == "months" \
            else start + timedelta(days=paid_n * (7 if o["freq_unit"] == "weeks" else 1) * max(1, int(o["freq_value"] or 1)))
        return due, round((o["final_payment"] or 0) / (periods or 1), 2)

    def due_score(days):
        """The closer the deadline, the higher: overdue 280+, today 260, tomorrow 230 ... 7 days 50."""
        return 280 + min(-days, 30) * 2 if days < 0 else 260 - days * 30

    def severity(score):
        return "critical" if score >= 250 else "high" if score >= 180 else "medium" if score >= 100 else "low"

    LEVEL_TEXT = {"A": "A · critical", "B": "B · low", "C": "C · watch"}

    @r.get("/priority")
    def priority(user=Depends(admin), conn=Depends(get_db)):
        """Dashboard cards, MOST URGENT FIRST (score). Supply: stock at its A / B / C level (A on top; deeper below the
        level = higher). Loans, payments, maintenance: due within 7 days or overdue (closer = higher). Also late bulk
        deliveries, deliveries today/tomorrow, receipts not updated / failed. Each card has an `action` the dashboard
        uses to fix it on the spot (restock, change levels, pay). X hides a card (until its situation changes)."""
        t0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        fmt_day = lambda d: d.strftime("%b %d").replace(" 0", " ")
        when = lambda days: "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days" if days > 0 else \
            f"{-days} day{'s' if days < -1 else ''} overdue"
        cards = []

        for i in conn.execute("SELECT * FROM items WHERE deleted_at IS NULL"):
            q, la, lb, lc = i["quantity"] or 0, i["level_a"], i["level_b"], i["level_c"]
            if i["qty_unknown"]:            # added with quantity N/A: ask for the count until one is typed (no X)
                sold = conn.execute("SELECT COALESCE(SUM(qty),0) FROM sale_items WHERE item_id=?", (i["id"],)).fetchone()[0]
                cards.append({"key": f"count-{i['id']}", "score": 210, "kind": "count", "sticky": True,
                              "title": f"Count the stock: {i['name']}",
                              "body": "Added without a quantity (N/A). Type how many there are; this card stays until you do."
                                      + (f" {sold:g} {i['unit']} sold since." if sold else ""),
                              "action": {"type": "count", "id": i["id"], "name": i["name"], "unit": i["unit"]}})
                continue
            lv = shop_db.stock_level(q, la, lb, lc)
            if not lv:
                continue
            limit = {"A": la, "B": lb, "C": lc}[lv] or 0
            depth = 1 if q <= 0 else max(0.0, min(1.0, 1 - q / limit)) if limit > 0 else 1
            score = {"A": 300, "B": 200, "C": 100}[lv] + round(depth * 50)
            levels = " · ".join(f"{k} ≤ {v:g}" for k, v in (("A", la), ("B", lb), ("C", lc)) if v is not None) or "no levels set"
            cards.append({"key": f"low-{i['id']}-{lv}-{t0.date()}", "score": score, "level": lv,
                          "title": f"{'Out of stock' if q <= 0 else 'Level ' + LEVEL_TEXT[lv]}: {i['name']}",
                          "body": f"{q:g} {i['unit']} left ({levels}). Restock it or change its levels so it leaves this list.",
                          "action": {"type": "item", "id": i["id"], "name": i["name"], "unit": i["unit"], "qty": q,
                                     "costPrice": i["cost_price"], "levelA": la, "levelB": lb, "levelC": lc}})

        for o in conn.execute("SELECT * FROM obligations WHERE status='active'"):
            due, each = obligation_due(conn, o)
            if not due:
                continue
            days = (due - t0).days
            if days > 7:
                continue
            cards.append({"key": f"obl-{o['id']}-{due.date()}", "score": due_score(days),
                          "title": f"{'Loan' if o['kind'] == 'loan' else 'Payment'} {when(days)}: {o['name']}",
                          "body": f"{money(each)} to {o['party'] or o['name']}, due {fmt_day(due)}.",
                          "action": {"type": "obligation", "id": f"{PREFIX[o['kind']]}-{o['id']}", "amount": each, "name": o["name"]}})

        for m in conn.execute("SELECT * FROM maintenance_items"):
            last, start = parse_day(m["last_paid"]), parse_day(m["start_date"])
            due = add_period(last, m["freq_value"], m["freq_unit"]) if last else (start or t0)
            days = (due - t0).days
            if days > 7:
                continue
            cards.append({"key": f"mnt-{m['id']}-{due.date()}", "score": due_score(days),
                          "title": f"{m['name']} {when(days)}",
                          "body": f"{money(m['amount'])} every {every(m['freq_value'], m['freq_unit'])}, due {fmt_day(due)}.",
                          "action": {"type": "maintenance", "id": f"mnt-{m['id']}", "amount": m["amount"], "name": m["name"]}})

        for o in conn.execute("SELECT * FROM bulk_orders WHERE status='active'"):
            end = parse_day(o["end_date"])
            left = conn.execute("SELECT COUNT(*) FROM bulk_order_items WHERE order_id=? AND delivered=0", (o["id"],)).fetchone()[0]
            if end and end < t0 and left:
                cards.append({"key": f"ord-{o['id']}-late", "score": 240, "title": f"Late delivery: {o['customer']}",
                              "body": f"{left} item(s) not delivered yet; the target end date was {fmt_day(end)}.",
                              "action": {"type": "order", "id": o["id"]}})
        for run in conn.execute("""SELECT r.*, o.customer, t.truck_name FROM bulk_order_runs r JOIN bulk_orders o ON o.id=r.order_id
                                   LEFT JOIN transports t ON t.id=r.transport_id WHERE o.status='active'"""):
            d = parse_day(run["date"])
            if d and t0 <= d <= t0 + timedelta(days=1) and not run["done"]:
                rd = run_dict(conn, run)
                when_ = "today" if d == t0 else "tomorrow"
                if rd["mode"] == "pickup":
                    title, body_ = f"Pick-up {when_}: {run['customer']}", "The customer comes for it with his own vehicle. Have the items ready."
                elif rd["mode"] == "other":
                    title = f"Delivery {when_}: {run['customer']}"
                    body_ = f"Delivered by {rd['carrier'] or 'someone else'} · {run['trips']} trip(s). Have the items ready."
                else:
                    crew_txt = (f" · driver {rd['driverName']}" if rd["driverName"] else " · no driver yet") + \
                        (f" + {len(rd['helpers'])} helper(s)" if rd["helpers"] else " · no helper yet")
                    title = f"Delivery {when_}: {run['customer']}"
                    body_ = f"{run['truck_name'] or 'Truck'} · {run['trips']} trip(s){crew_txt}. Planner shows who goes."
                cards.append({"key": f"run-{run['id']}-{d.date()}", "score": 190 if d == t0 else 150, "title": title,
                              "body": body_, "action": {"type": "order", "id": run["order_id"]}})
        for run in conn.execute("""SELECT r.*, t.truck_name FROM bulk_order_runs r LEFT JOIN transports t ON t.id=r.transport_id
                                   WHERE r.order_id IS NULL AND COALESCE(r.done,0)=0"""):
            d = parse_day(run["date"])
            if d and t0 <= d <= t0 + timedelta(days=1):
                rd = run_dict(conn, run)
                crew_txt = (f"driver {rd['driverName']}" if rd["driverName"] else "no driver yet") + \
                    (f" + {len(rd['helpers'])} helper(s)" if rd["helpers"] else "")
                cards.append({"key": f"trip-{run['id']}-{d.date()}", "score": 185 if d == t0 else 140,
                              "title": f"Truck {'today' if d == t0 else 'tomorrow'}{' ' + rd['time'] if rd['time'] else ''}: {rd['label']}",
                              "body": f"{run['truck_name'] or 'Truck'} · {crew_txt}{' · ' + rd['note'] if rd['note'] else ''}. The Planner shows it.",
                              "action": {"type": "info"}})

        for x in conn.execute("""SELECT d.*, COALESCE(u.display_name, u.username) who FROM delivery_drafts d
                                 LEFT JOIN users u ON u.id=d.user_id WHERE d.status='new' ORDER BY d.id"""):
            n = len((_json_mod.loads(x["data"] or "{}") or {}).get("rows", []))
            cards.append({"key": f"dlv-{x['id']}", "score": 420, "kind": "delivery",
                          "title": f"New delivery order from {x['who'] or 'a phone'}: {x['customer'] or 'customer not read'}",
                          "body": f"{n} item(s) read from the receipt photo{' · deliver ' + x['delivery_date'] if x['delivery_date'] else ''}. "
                                  "Open it, check the details and press Apply.",
                          "action": {"type": "delivery", "id": x["id"]}})

        for u in conn.execute("""SELECT x.*, COALESCE(us.display_name, us.username) who, r.created rc_created
                                 FROM receipt_unmatched x LEFT JOIN users us ON us.id=x.req_by
                                 LEFT JOIN receipts r ON r.id=x.receipt_id WHERE x.req_at IS NOT NULL ORDER BY x.req_at"""):
            amount = (u["req_price"] or 0) * (u["req_qty"] or 0)
            cards.append({"key": f"req-{u['id']}", "score": 230, "kind": "request", "sticky": True,
                          "title": f"New item from {u['who'] or 'a phone'}: {u['req_name']}",
                          "body": f"{u['req_qty']:g} {u['req_unit'] or ''} at {peso_txt(u['req_price'] or 0)} = {peso_txt(amount)}"
                                  f"{' on receipt ' + shop_db.receipt_label(u['rc_created']) if u['rc_created'] else ''}. "
                                  "Open Supply to accept it (it is added; the sales person then sells it with Update) or remove it."
                                  + (f" They say {u['req_stock']:g} are left." if u["req_stock"] is not None else ""),
                          "action": {"type": "request", "id": u["id"]}})

        failed = conn.execute("SELECT COUNT(*) FROM receipts WHERE status='failed'").fetchone()[0]
        if failed:
            cards.append({"key": f"fail-{t0.date()}-{failed}", "score": 160, "title": f"{failed} receipt photo(s) failed",
                          "body": "The OCR could not read them. The sales person can press 'Read again' on the phone.", "action": {"type": "info"}})
        waiting = conn.execute("""SELECT COUNT(*) FROM receipts r WHERE r.status='done' AND EXISTS
                                  (SELECT 1 FROM receipt_lines l WHERE l.receipt_id=r.id AND l.sold=0)""").fetchone()[0]
        if waiting:
            cards.append({"key": f"rcpt-{t0.date()}-{waiting}", "score": 90, "title": f"{waiting} receipt(s) not updated",
                          "body": "Photos were read but the sales person has not pressed Update on the phone yet, so stock was not deducted.",
                          "action": {"type": "info"}})

        hidden = {x[0] for x in conn.execute("SELECT key FROM priority_dismissed")}
        cards = [c for c in cards if c["key"] not in hidden]
        cards.sort(key=lambda c: -c["score"])
        for c in cards:
            c["severity"] = severity(c["score"])
            c["size"] = {"critical": "lg", "high": "md"}.get(c["severity"], "sm")
        return cards

    class KeyIn(BaseModel):
        key: str

    @r.post("/priority/dismiss")
    def dismiss(body: KeyIn, user=Depends(admin), conn=Depends(get_db)):
        if body.key.startswith("count-"):
            raise HTTPException(409, "This card stays until the stock is counted: type the count")
        if body.key.startswith("req-"):
            raise HTTPException(409, "This card stays until the new item is accepted or removed in Supply")
        with conn:
            conn.execute("INSERT OR REPLACE INTO priority_dismissed(key, dismissed_at) VALUES(?,?)", (body.key, now()))
            conn.execute("DELETE FROM priority_dismissed WHERE dismissed_at < ?", ((datetime.now() - timedelta(days=60)).isoformat(),))
        return {"ok": True}

    # ---------------- attendance ----------------
    class AttendIn(BaseModel):
        employeeId: int
        present: bool
        date: Optional[str] = None

    @r.get("/attendance")
    def attendance(date: Optional[str] = None, user=Depends(admin), conn=Depends(get_db)):
        d = date or today()
        rows = conn.execute("""SELECT e.id, e.name, e.position, e.photo, e.temporary, e.per_day_rate, e.rate_per_hour,
                                      a.time_in, w.ot_hours, w.ut_hours FROM employees e
                               LEFT JOIN attendance a ON a.employee_id=e.id AND a.date=?
                               LEFT JOIN work_hours w ON w.employee_id=e.id AND w.date=?
                               WHERE e.active=1 ORDER BY e.temporary, e.name""", (d, d)).fetchall()
        return {"date": d, "staff": [{"employeeId": x["id"], "name": x["name"], "role": x["position"] or "",
                                      "hasPhoto": bool(x["photo"]), "checked": x["time_in"] is not None, "time": x["time_in"] or "",
                                      "temporary": bool(x["temporary"]), "perDayRate": x["per_day_rate"],
                                      "ratePerHour": x["rate_per_hour"], "ot": x["ot_hours"] or 0, "ut": x["ut_hours"] or 0}
                                     for x in rows]}

    def mark_present(conn, emp_id, d, present, time_in=None):
        """check in / out one person on one day; keeps employees.present_days (Cash Flow) in step"""
        had = conn.execute("SELECT 1 FROM attendance WHERE employee_id=? AND date=?", (emp_id, d)).fetchone()
        if present and not had:
            conn.execute("INSERT INTO attendance(employee_id,date,time_in) VALUES(?,?,?)",
                         (emp_id, d, time_in or datetime.now().strftime("%H:%M")))
            conn.execute("UPDATE employees SET present_days = present_days + 1 WHERE id=?", (emp_id,))
        elif present and had and time_in:
            conn.execute("UPDATE attendance SET time_in=? WHERE employee_id=? AND date=?", (time_in, emp_id, d))
        elif not present and had:
            conn.execute("DELETE FROM attendance WHERE employee_id=? AND date=?", (emp_id, d))
            conn.execute("UPDATE employees SET present_days = MAX(0, present_days - 1) WHERE id=?", (emp_id,))

    def set_hours(conn, emp_id, d, ot=None, ut=None, note_=None):
        """overtime / undertime of one day; the Cash Flow totals move by the difference"""
        old = conn.execute("SELECT ot_hours, ut_hours FROM work_hours WHERE employee_id=? AND date=?", (emp_id, d)).fetchone()
        o_ot, o_ut = (old[0], old[1]) if old else (0, 0)
        n_ot = max(0.0, float(o_ot if ot is None else ot))
        n_ut = max(0.0, float(o_ut if ut is None else ut))
        if n_ot == 0 and n_ut == 0:
            conn.execute("DELETE FROM work_hours WHERE employee_id=? AND date=?", (emp_id, d))
        else:
            conn.execute("""INSERT INTO work_hours(employee_id,date,ot_hours,ut_hours,note) VALUES(?,?,?,?,?)
                            ON CONFLICT(employee_id,date) DO UPDATE SET ot_hours=excluded.ot_hours, ut_hours=excluded.ut_hours,
                            note=COALESCE(excluded.note, work_hours.note)""", (emp_id, d, n_ot, n_ut, note_))
        conn.execute("""UPDATE employees SET overtime_hours = MAX(0, overtime_hours + ?),
                        undertime_hours = MAX(0, undertime_hours + ?) WHERE id=?""", (n_ot - o_ot, n_ut - o_ut, emp_id))
        return n_ot, n_ut

    @r.get("/attendance/range")
    def attendance_range(start: str, end: str, hidden: bool = False, user=Depends(admin), conn=Depends(get_db)):
        """General attendance: everyone x every day between start and end (max 62 days), to fill a whole month at once.
        Removed people who worked in those days only show with hidden=1 (hiddenCount says how many there are)."""
        check_day(start), check_day(end)
        a, b = datetime.strptime(start, "%Y-%m-%d"), datetime.strptime(end, "%Y-%m-%d")
        if b < a or (b - a).days > 61:
            raise HTTPException(422, "Pick at most 62 days")
        days = [(a + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((b - a).days + 1)]
        everyone = conn.execute("""SELECT * FROM employees e WHERE e.active=1 OR EXISTS (SELECT 1 FROM attendance x
                                   WHERE x.employee_id=e.id AND x.date BETWEEN ? AND ?) ORDER BY e.temporary, e.name""", (start, end)).fetchall()
        staff = [e for e in everyone if e["active"] or hidden]
        present = {}
        for x in conn.execute("SELECT employee_id, date FROM attendance WHERE date BETWEEN ? AND ?", (start, end)):
            present.setdefault(str(x["employee_id"]), []).append(x["date"])
        return {"start": start, "end": end, "days": days, "present": present,
                "hiddenCount": sum(1 for e in everyone if not e["active"]),
                "staff": [{"employeeId": e["id"], "name": e["name"], "role": e["position"] or "", "temporary": bool(e["temporary"]),
                           "active": bool(e["active"]), "hasPhoto": bool(e["photo"])} for e in staff]}

    class MarkIn(BaseModel):
        employeeId: int
        date: str
        present: bool

    class BulkIn(BaseModel):
        marks: list[MarkIn]

    @r.post("/attendance/bulk")
    def attendance_bulk(body: BulkIn, user=Depends(admin), conn=Depends(get_db)):
        """Save the General attendance grid: only the boxes that changed are sent. A day checked here has no time in."""
        if len(body.marks) > 5000:
            raise HTTPException(422, "Too many boxes at once")
        added = removed = 0
        with conn:
            for m in body.marks:
                check_day(m.date)
                if not conn.execute("SELECT 1 FROM employees WHERE id=?", (m.employeeId,)).fetchone():
                    raise HTTPException(404, "Employee not found")
                had = conn.execute("SELECT 1 FROM attendance WHERE employee_id=? AND date=?", (m.employeeId, m.date)).fetchone()
                if m.present and not had:
                    conn.execute("INSERT INTO attendance(employee_id,date,time_in) VALUES(?,?,'')", (m.employeeId, m.date))
                    conn.execute("UPDATE employees SET present_days = present_days + 1 WHERE id=?", (m.employeeId,))
                    added += 1
                elif not m.present and had:
                    mark_present(conn, m.employeeId, m.date, False)
                    removed += 1
        note(conn, user, "General attendance", f"{added} day(s) checked, {removed} unchecked")
        return {"added": added, "removed": removed}

    class TempIn(BaseModel):
        name: str
        role: str = "Helper"
        perDayRate: float = 0
        ratePerHour: float = 0
        presentToday: bool = True

    @r.post("/employees/temporary")
    def add_temporary(body: TempIn, user=Depends(admin), conn=Depends(get_db)):
        """Extra help for a few days: a normal employee row marked temporary (shows in Cash Flow and the payslip too)."""
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Type the person's name")
        with conn:
            if body.role.strip():
                conn.execute("INSERT OR IGNORE INTO positions(name) VALUES(?)", (body.role.strip(),))
            same = conn.execute("SELECT id FROM employees WHERE name=? COLLATE NOCASE AND temporary=1", (name,)).fetchone()
            if same:                                   # came back to help again
                n = same[0]
                conn.execute("UPDATE employees SET active=1, position=?, per_day_rate=?, rate_per_hour=? WHERE id=?",
                             (body.role.strip(), body.perDayRate, body.ratePerHour, n))
            else:
                n = conn.execute("""INSERT INTO employees(name,position,per_day_rate,rate_per_hour,temporary,active,created)
                                    VALUES(?,?,?,?,1,1,?)""", (name, body.role.strip(), body.perDayRate, body.ratePerHour, now())).lastrowid
            if body.presentToday:
                mark_present(conn, n, today(), True)
        note(conn, user, "Temporary staff", f"{name} ({body.role.strip() or 'helper'}, {peso_txt(body.perDayRate)}/day)")
        return attendance(None, user, conn)

    @r.post("/employees/{n}/end-temporary")
    def end_temporary(n: int, user=Depends(admin), conn=Depends(get_db)):
        """Temporary help is done: off the attendance list. Their days stay for the payslip."""
        e = conn.execute("SELECT * FROM employees WHERE id=? AND temporary=1", (n,)).fetchone()
        if not e:
            raise HTTPException(404, "Temporary person not found")
        with conn:
            conn.execute("UPDATE employees SET active=0, removed=? WHERE id=?", (today(), n))
        note(conn, user, "Temporary staff", f"{e['name']} done")
        return attendance(None, user, conn)

    # ---------------- removing a person: first what the shop still owes them ----------------
    # Temporary -> Done and Cash Flow -> delete open a popup with this: every day worked, overtime, bonuses, cash
    # advances, what was already paid and what is STILL OWED. "Pay & remove" records that last pay as payroll; the
    # person is then hidden (not deleted): their days stay in the books, "Show hidden" lists them, "Bring back" undoes it.
    def get_emp(conn, n):
        e = conn.execute("SELECT * FROM employees WHERE id=?", (n,)).fetchone()
        if not e:
            raise HTTPException(404, "Employee not found")
        return e

    def settlement_of(conn, e):
        p_ = payslip_for(conn, e, "0000-01-01", "9999-12-31")      # every record of this person
        dates = [d["date"] for d in p_["days"]]
        last_pay = conn.execute("""SELECT date, amount FROM employee_payments WHERE employee_id=? AND kind='payroll'
                                   ORDER BY date DESC, id DESC LIMIT 1""", (e["id"],)).fetchone()
        runs = [{"runId": x["id"], "customer": x["customer"] or trip_label(x), "date": x["date"] or "", "role": x["role"]}
                for x in conn.execute("""SELECT r.id, r.date, r.purpose, r.place, o.customer,
                                                CASE WHEN r.driver_id=:e THEN 'driver' ELSE 'helper' END role
                                         FROM bulk_order_runs r LEFT JOIN bulk_orders o ON o.id=r.order_id
                                         WHERE (o.status='active' OR r.order_id IS NULL) AND COALESCE(r.done,0)=0
                                           AND (r.date IS NULL OR r.date='' OR r.date>=:t)
                                           AND (r.driver_id=:e OR EXISTS (SELECT 1 FROM run_crew c WHERE c.run_id=r.id AND c.employee_id=:e))
                                         ORDER BY r.date""", {"e": e["id"], "t": today()})]
        login = conn.execute("SELECT username, is_admin FROM users WHERE id=?", (e["user_id"],)).fetchone() if e["user_id"] else None
        bal = p_["balance"]
        keep = ("employeeId", "name", "role", "temporary", "active", "hasPhoto", "perDayRate", "ratePerHour", "present",
                "otHours", "utHours", "gross", "otPay", "utLess", "advanceTotal", "bonusTotal", "paidTotal", "net")
        return {**{k: p_[k] for k in keep}, "from": dates[0] if dates else "", "to": dates[-1] if dates else "",
                "owed": round(max(0.0, bal), 2), "theyOwe": round(max(0.0, -bal), 2),
                "lastPay": {"date": last_pay["date"], "amount": last_pay["amount"]} if last_pay else None,
                "login": login["username"] if login and not login["is_admin"] else "", "futureRuns": runs,
                "removed": (e["removed"] if "removed" in e.keys() else None) or ""}

    @r.get("/employees/removed")
    def removed_employees(user=Depends(admin), conn=Depends(get_db)):
        """'Show hidden': everyone removed (temporary help that is done, people who left), newest first."""
        out = []
        for e in conn.execute("SELECT * FROM employees WHERE active=0 ORDER BY COALESCE(removed,'') DESC, name").fetchall():
            st = settlement_of(conn, e)
            out.append({k: st[k] for k in ("employeeId", "name", "role", "temporary", "hasPhoto", "owed", "theyOwe", "present",
                                           "from", "to", "login", "removed", "lastPay")})
        return out

    @r.get("/employees/{n}/settlement")
    def employee_settlement(n: int, user=Depends(admin), conn=Depends(get_db)):
        return settlement_of(conn, get_emp(conn, n))

    class RemoveIn(BaseModel):
        pay: float = 0          # the last pay handed over now (recorded as payroll: "Final pay")
        note: str = ""

    @r.post("/employees/{n}/remove")
    def remove_employee(n: int, body: RemoveIn, user=Depends(admin), conn=Depends(get_db)):
        e = get_emp(conn, n)
        if body.pay < 0 or body.pay > 10_000_000:
            raise HTTPException(422, "The pay must be 0 or more")
        st = settlement_of(conn, e)
        t = today()
        with conn:
            if body.pay > 0:
                conn.execute("INSERT INTO employee_payments(employee_id,kind,amount,note,date) VALUES(?,'payroll',?,?,?)",
                             (n, round(body.pay, 2), ("Final pay" + (f" - {body.note.strip()}" if body.note.strip() else ""))[:200], t))
            conn.execute("UPDATE employees SET active=0, removed=? WHERE id=?", (t, n))
            if e["user_id"]:                            # their phone login stops; past sales stay (Bring back turns it on)
                if conn.execute("UPDATE users SET active=0 WHERE id=? AND is_admin=0", (e["user_id"],)).rowcount:
                    conn.execute("DELETE FROM sessions WHERE user_id=?", (e["user_id"],))
            for x in st["futureRuns"]:                  # off the deliveries that are still to come
                conn.execute("DELETE FROM run_crew WHERE run_id=? AND employee_id=?", (x["runId"], n))
                conn.execute("UPDATE bulk_order_runs SET driver_id=NULL WHERE id=? AND driver_id=?", (x["runId"], n))
        left = st["owed"] - body.pay
        note(conn, user, "Removed", f"{e['name']}" + (f": final pay {peso_txt(body.pay)}" if body.pay > 0 else "")
             + (f", still owed {peso_txt(left)}" if left > 0.005 else ""))
        return settlement_of(conn, get_emp(conn, n))

    @r.post("/employees/{n}/restore")
    def restore_employee(n: int, user=Depends(admin), conn=Depends(get_db)):
        """Bring back a removed person (and their phone login)."""
        e = get_emp(conn, n)
        with conn:
            conn.execute("UPDATE employees SET active=1, removed=NULL WHERE id=?", (n,))
            if e["user_id"]:
                conn.execute("UPDATE users SET active=1 WHERE id=?", (e["user_id"],))
        note(conn, user, "Brought back", e["name"])
        return emp_entry(conn, get_emp(conn, n))

    class HoursRow(BaseModel):
        employeeId: int
        ot: Optional[float] = None
        ut: Optional[float] = None

    class HoursIn(BaseModel):
        date: Optional[str] = None
        rows: list[HoursRow]
        note: Optional[str] = None

    @r.post("/work-hours")
    def save_hours(body: HoursIn, user=Depends(admin), conn=Depends(get_db)):
        """Dashboard -> Overtime: hours for the chosen people on one day (Apply to all = same hours for everyone)."""
        d = check_day(body.date) if body.date else today()
        names = []
        with conn:
            for x in body.rows:
                e = conn.execute("SELECT name FROM employees WHERE id=?", (x.employeeId,)).fetchone()
                if not e:
                    raise HTTPException(404, "Employee not found")
                if (x.ot or 0) < 0 or (x.ut or 0) < 0 or (x.ot or 0) > 24 or (x.ut or 0) > 24:
                    raise HTTPException(422, "Hours must be between 0 and 24")
                ot, ut = set_hours(conn, x.employeeId, d, x.ot, x.ut, body.note)
                names.append(f"{e['name']} {'+' + format(ot, 'g') + 'h OT' if ot else ''}{' -' + format(ut, 'g') + 'h UT' if ut else ''}".strip())
        if names:
            note(conn, user, "Overtime", f"{d}: " + ", ".join(names)[:250])
        return attendance(d, user, conn)

    def payslip_for(conn, e, start, end):
        att = {x["date"]: x["time_in"] for x in conn.execute(
            "SELECT date, time_in FROM attendance WHERE employee_id=? AND date BETWEEN ? AND ?", (e["id"], start, end))}
        hrs = {x["date"]: x for x in conn.execute(
            "SELECT * FROM work_hours WHERE employee_id=? AND date BETWEEN ? AND ?", (e["id"], start, end))}
        rate, hour = e["per_day_rate"] or 0, e["rate_per_hour"] or 0
        days = []
        for d in sorted(set(att) | set(hrs)):
            ot = hrs[d]["ot_hours"] if d in hrs else 0
            ut = hrs[d]["ut_hours"] if d in hrs else 0
            pay = (rate if d in att else 0) + ot * hour * 1.25 - ut * hour
            days.append({"date": d, "present": d in att, "timeIn": att.get(d) or "", "ot": ot, "ut": ut,
                         "note": (hrs[d]["note"] if d in hrs else "") or "", "pay": round(pay, 2)})
        log = lambda sql: [{"date": x["date"], "amount": x["amount"], "note": x["note"] or ""}
                           for x in conn.execute(sql, (e["id"], start, end))]
        adv = log("SELECT * FROM employee_cash_advances WHERE employee_id=? AND date BETWEEN ? AND ? ORDER BY date")
        bonus = log("SELECT * FROM employee_payments WHERE employee_id=? AND kind='bonus' AND date BETWEEN ? AND ? ORDER BY date")
        paid = log("SELECT * FROM employee_payments WHERE employee_id=? AND kind='payroll' AND date BETWEEN ? AND ? ORDER BY date")
        n_days = sum(1 for x in days if x["present"])
        ot_h, ut_h = sum(x["ot"] for x in days), sum(x["ut"] for x in days)
        gross, ot_pay, ut_less = n_days * rate, ot_h * hour * 1.25, ut_h * hour
        adv_t, bonus_t, paid_t = sum(x["amount"] for x in adv), sum(x["amount"] for x in bonus), sum(x["amount"] for x in paid)
        net = round(gross + ot_pay + bonus_t - ut_less - adv_t, 2)       # rounded FIRST: paying the balance shown leaves 0
        return {"employeeId": e["id"], "name": e["name"], "role": e["position"] or "", "temporary": bool(e["temporary"]),
                "active": bool(e["active"]), "hasPhoto": bool(e["photo"]), "perDayRate": rate, "ratePerHour": hour,
                "days": days, "present": n_days, "otHours": ot_h, "utHours": ut_h, "gross": round(gross, 2),
                "otPay": round(ot_pay, 2), "utLess": round(ut_less, 2), "advances": adv, "advanceTotal": round(adv_t, 2),
                "bonuses": bonus, "bonusTotal": round(bonus_t, 2), "paid": paid, "paidTotal": round(paid_t, 2),
                "net": net, "balance": round(net - paid_t, 2)}

    @r.get("/payslip")
    def payslip(start: str, end: str, hidden: bool = False, user=Depends(admin), conn=Depends(get_db)):
        """Each employee's pay between start and end from the day records: days present x day rate + overtime
        (hour rate x 1.25) - undertime (hour rate) + bonuses - cash advances. balance = net - payroll already paid.
        People who were removed (settled and hidden) only show with hidden=1; the totals of the hidden ones come apart."""
        check_day(start), check_day(end)
        rows = conn.execute("""SELECT * FROM employees e WHERE e.active=1 OR EXISTS (SELECT 1 FROM attendance a
                               WHERE a.employee_id=e.id AND a.date BETWEEN :s AND :e) OR EXISTS (SELECT 1 FROM work_hours w
                               WHERE w.employee_id=e.id AND w.date BETWEEN :s AND :e) OR EXISTS (SELECT 1 FROM employee_payments x
                               WHERE x.employee_id=e.id AND x.date BETWEEN :s AND :e) ORDER BY e.temporary, e.name""",
                            {"s": start, "e": end}).fetchall()
        everyone = [payslip_for(conn, e, start, end) for e in rows]
        gone = [p_ for p_ in everyone if not p_["active"]]
        people = everyone if hidden else [p_ for p_ in everyone if p_["active"]]
        return {"start": start, "end": end, "people": people, "total": round(sum(p_["net"] for p_ in people), 2),
                "balance": round(sum(p_["balance"] for p_ in people), 2), "hiddenCount": len(gone),
                "hiddenTotal": round(sum(p_["net"] for p_ in gone), 2),
                "hiddenOwed": round(sum(max(0.0, p_["balance"]) for p_ in gone), 2)}

    class DayEditIn(BaseModel):
        employeeId: int
        date: str
        present: bool
        timeIn: Optional[str] = None
        ot: float = 0
        ut: float = 0

    @r.put("/payslip/day")
    def payslip_day(body: DayEditIn, user=Depends(admin), conn=Depends(get_db)):
        """Payslip -> Edit: fix one day of one person (present, time in, overtime, undertime)."""
        d = check_day(body.date)
        e = conn.execute("SELECT * FROM employees WHERE id=?", (body.employeeId,)).fetchone()
        if not e:
            raise HTTPException(404, "Employee not found")
        if not (0 <= body.ot <= 24 and 0 <= body.ut <= 24):
            raise HTTPException(422, "Hours must be between 0 and 24")
        t_in = None
        if body.timeIn:
            try:
                t_in = datetime.strptime(body.timeIn.strip(), "%H:%M").strftime("%H:%M")
            except ValueError:
                raise HTTPException(422, "Time in is HH:MM (24-hour), e.g. 07:30")
        with conn:
            mark_present(conn, e["id"], d, body.present, t_in)
            set_hours(conn, e["id"], d, body.ot, body.ut)
        note(conn, user, "Payslip edit", f"{e['name']} {d}: {'present' if body.present else 'absent'}"
                                          f"{', OT ' + format(body.ot, 'g') + 'h' if body.ot else ''}{', UT ' + format(body.ut, 'g') + 'h' if body.ut else ''}")
        return {"ok": True}

    @r.post("/attendance")
    def set_attendance(body: AttendIn, user=Depends(admin), conn=Depends(get_db)):
        d = body.date or today()
        if not conn.execute("SELECT 1 FROM employees WHERE id=?", (body.employeeId,)).fetchone():
            raise HTTPException(404, "Employee not found")
        with conn:
            mark_present(conn, body.employeeId, d, body.present)
        who = conn.execute("SELECT name FROM employees WHERE id=?", (body.employeeId,)).fetchone()[0]
        note(conn, user, "Attendance", f"{who} {'present' if body.present else 'unchecked'} ({d})")
        return attendance(d, user, conn)

    # ---------------- cash on hand ----------------
    class CashIn(BaseModel):
        direction: str
        amount: float
        note: Optional[str] = None

    def one(conn, sql, *a):
        return conn.execute(sql, a).fetchone()[0] or 0

    @r.get("/cash")
    def cash(user=Depends(admin), conn=Depends(get_db)):
        """Cash on hand = money in - money out, using only what is recorded in the database."""
        inn = {"sales": one(conn, "SELECT SUM(total) FROM sales"),
               "orders": one(conn, "SELECT SUM(amount) FROM bulk_order_payments"),
               "manual": one(conn, "SELECT SUM(amount) FROM cash_entries WHERE direction='in'")}
        out = {"payroll": one(conn, "SELECT SUM(amount) FROM employee_payments"),
               "obligations": one(conn, "SELECT SUM(amount) FROM obligation_payments"),
               "maintenance": one(conn, "SELECT SUM(amount) FROM maintenance_payments"),
               "purchases": one(conn, "SELECT SUM(qty*unit_cost) FROM stock_purchases"),
               "manual": one(conn, "SELECT SUM(amount) FROM cash_entries WHERE direction='out'")}
        t = today()
        in_today = one(conn, "SELECT SUM(total) FROM sales WHERE substr(created,1,10)=?", t) + \
            one(conn, "SELECT SUM(amount) FROM bulk_order_payments WHERE date=?", t) + \
            one(conn, "SELECT SUM(amount) FROM cash_entries WHERE direction='in' AND date=?", t)
        return {"onHand": sum(inn.values()) - sum(out.values()), "in": inn, "out": out, "inToday": in_today,
                "entries": [dict(x) for x in conn.execute("SELECT * FROM cash_entries ORDER BY id DESC LIMIT 20")]}

    # ---------------- Progression: cash in vs cash out, split by what it was for ----------------
    @r.get("/cashflow-breakdown")
    def cashflow_breakdown(period: str = "month", user=Depends(admin), conn=Depends(get_db)):
        """period = month (this month) | 3m | 12m | all. Every slice lists the payments it is made of (newest first)."""
        t = datetime.now().date()
        first = t.replace(day=1)
        back = {"month": 0, "3m": 2, "12m": 11}.get(period)
        if period == "all":
            start = "0000-00-00"
        elif back is None:
            raise HTTPException(422, "period must be month, 3m, 12m or all")
        else:
            m = first.month - back
            start = first.replace(year=first.year + (m - 1) // 12, month=(m - 1) % 12 + 1).isoformat()
        by_day = period == "month"
        series = {}

        def bucket(d):
            return d[:10] if by_day else d[:7]

        def slice_(key, label, kind, rows):
            rows = sorted(rows, key=lambda x: x["date"], reverse=True)
            for x in rows:
                b = series.setdefault(bucket(x["date"]), {"in": 0, "out": 0})
                b[kind] += x["amount"]
            total = sum(x["amount"] for x in rows)
            return {"key": key, "label": label, "kind": kind, "amount": round(total, 2), "count": len(rows),
                    "items": [{**x, "amount": round(x["amount"], 2)} for x in rows[:200]]} if total > 0 else None

        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        ins, outs = [], []
        # money in: walk-in sales per sales person (one row per day), bulk order payments, cash added by hand
        people = {}
        for x in q("""SELECT substr(s.created,1,10) d, COALESCE(u.display_name,u.username,'Desktop') person,
                             COUNT(*) n, SUM(s.total) amt
                      FROM sales s LEFT JOIN users u ON u.id=s.user_id WHERE substr(s.created,1,10)>=?
                      GROUP BY d, person""", start):
            people.setdefault(x["person"], []).append({"date": x["d"], "label": f"{x['n']} sale(s)", "amount": x["amt"] or 0})
        for person, rows in people.items():
            ins.append(slice_(f"sales:{person}", f"Sales — {person}", "in", rows))
        ins.append(slice_("orders", "Bulk order payments", "in", [
            {"date": x["date"], "label": x["customer"], "note": x["note"] or "", "amount": x["amount"]}
            for x in q("""SELECT p.*, o.customer FROM bulk_order_payments p JOIN bulk_orders o ON o.id=p.order_id
                          WHERE p.date>=?""", start)]))
        ins.append(slice_("cash-in", "Other cash in", "in", [
            {"date": x["date"], "label": x["note"] or "Cash in", "amount": x["amount"]}
            for x in q("SELECT * FROM cash_entries WHERE direction='in' AND date>=?", start)]))
        # money out. Cash Flow items count what is DUE in the period (loan installments, bills, maintenance, salaries
        # from attendance, monthly benefits), even when nobody logged the payment yet; each due date is marked paid or
        # still to pay. If more was actually paid than was due, the payments count instead.
        end_d = (first.replace(year=first.year + first.month // 12, month=first.month % 12 + 1) - timedelta(days=1))
        win_from = parse_day(start) if start != "0000-00-00" else None
        win_to = datetime.combine(end_d, datetime.min.time())
        today0 = datetime.combine(t, datetime.min.time())
        in_win = lambda d: (win_from is None or d >= win_from) and d <= win_to
        status = lambda d, paid: "paid" if paid else ("overdue" if d < today0 else "to pay")

        def planned(key, label, dues, payments, row_label=None):
            """dues: [(date, amount, paid?)] scheduled in the window; payments: what was really paid in the window."""
            due_amt = sum(a for _, a, _ in dues)
            paid_amt = sum(x["amount"] for x in payments)
            if due_amt <= 0 and paid_amt <= 0:
                return None
            if due_amt >= paid_amt:
                rows = [{"date": d.strftime("%Y-%m-%d"), "label": row_label or label, "note": status(d, ok), "amount": a,
                         "status": status(d, ok)}
                        for d, a, ok in dues]
                unpaid = sum(a for _, a, ok in dues if not ok)
            else:
                rows = [{**x, "status": "paid"} for x in payments]
                unpaid = 0
            sl = slice_(key, label, "out", rows)
            if sl:
                sl.update({"due": round(max(due_amt, paid_amt), 2), "unpaid": round(unpaid, 2), "planned": True})
            return sl

        def schedule(first_due, v, unit, last=None, count=None, until=None):
            d, n, until = first_due, 0, until or win_to
            while d <= until and (last is None or d <= last) and (count is None or n < count) and n < 600:
                yield n, d
                d, n = add_period(first_due, v * (n + 1), unit) if unit == "months" else add_period(d, v, unit), n + 1

        # the months of the window (period=all: from the first data)
        months = []
        mm = parse_day(start) if start != "0000-00-00" else None
        if mm is None:
            firsts = [parse_day(x[0]) for x in q("SELECT MIN(date) FROM attendance UNION ALL SELECT MIN(substr(created,1,10)) FROM sales "
                                                 "UNION ALL SELECT MIN(start_date) FROM maintenance_items")]
            firsts = [x for x in firsts if x]
            mm = min(firsts).replace(day=1) if firsts else datetime.combine(first, datetime.min.time())
        while mm <= win_to and len(months) < 240:
            months.append(mm)
            mm = add_period(mm, 1, "months")

        def shared(key, label, dues_all, v, unit):
            """A cost due every few months (change oil every 6 months) counts as its MONTHLY SHARE in every month:
            P2,500 every 6 months = P417 a month, so a month shows what the cost really takes, not P2,500 at once.
            Each month's share is for the next due date after that month; it is 'paid' once that due date is paid."""
            rows, n_months = [], months_in_cycle(v, unit)
            for m in months:
                m_end = add_period(m, 1, "months") - timedelta(days=1)
                tgt = next(((d, a, ok) for d, a, ok in dues_all if d > m_end), None)
                if not tgt or add_period_back(tgt[0], v, unit) > m_end:
                    continue                              # before the first saving cycle / after the last due
                d, a, ok = tgt
                st = "paid" if ok else ("overdue" if d < today0 else "to pay")
                rows.append({"date": m.strftime("%Y-%m-%d"), "label": label, "amount": round(monthly_share(a, v, unit), 2),
                             "note": f"monthly share: {peso_txt(a)} every {every(v, unit)} (1/{n_months:g}), due {d:%b %d, %Y}",
                             "status": st})
            sl = slice_(key, label, "out", rows)
            if sl:
                sl.update({"due": sl["amount"], "unpaid": round(sum(r["amount"] for r in rows if r["status"] != "paid"), 2),
                           "planned": True, "shared": True, "every": every(v, unit), "full": round(dues_all[0][1], 2)})
            return sl

        for o in q("SELECT * FROM obligations WHERE status<>'paused' ORDER BY id"):
            st = parse_day(o["start_date"])
            pays = q("SELECT * FROM obligation_payments WHERE obligation_id=? ORDER BY date, id", o["id"])
            in_pays = [{"date": x["date"], "label": o["party"] or o["name"], "note": x["note"] or "", "amount": x["amount"]}
                       for x in pays if parse_day(x["date"]) and in_win(parse_day(x["date"]))]
            dues = []
            if st and o["final_payment"]:
                periods = obl_periods(o, st)                   # None = no end date: it keeps coming back
                each = round((o["final_payment"] or 0) / (periods or 1), 2)
                paid_n = len(pays)
                dues_all = [(d, each, n < paid_n or o["status"] == "completed")
                            for n, d in schedule(st, o["freq_value"], o["freq_unit"], count=periods, until=datetime(2200, 1, 1))]
                if is_long(o["freq_value"], o["freq_unit"]):          # e.g. a yearly permit: its monthly share
                    outs.append(shared(f"obl:{o['id']}", o["name"], dues_all, o["freq_value"], o["freq_unit"]))
                    continue
                dues = [x for x in dues_all if in_win(x[0])]
            outs.append(planned(f"obl:{o['id']}", o["name"] + (" (loan)" if o["kind"] == "loan" else ""), dues, in_pays,
                                f"{'Installment' if o['kind'] == 'loan' else 'Payment'}{' to ' + o['party'] if o['party'] else ''}"))

        for m_ in q("SELECT * FROM maintenance_items ORDER BY id"):
            pays = [{"date": x["date"], "label": m_["name"], "amount": x["amount"]}
                    for x in q("SELECT * FROM maintenance_payments WHERE item_id=?", m_["id"])]
            in_pays = [x for x in pays if parse_day(x["date"]) and in_win(parse_day(x["date"]))]
            st = parse_day(m_["start_date"]) or parse_day(m_["last_paid"])
            last_paid = parse_day(m_["last_paid"])
            if st and is_long(m_["freq_value"], m_["freq_unit"]):  # change oil every 6 months: 1/6 each month
                dues_all = [(d, m_["amount"] or 0, bool(last_paid and d <= last_paid))
                            for _, d in schedule(st, m_["freq_value"], m_["freq_unit"], until=win_to + timedelta(days=1200))]
                outs.append(shared(f"mnt:{m_['id']}", m_["name"], dues_all, m_["freq_value"], m_["freq_unit"]))
                continue
            dues = [(d, m_["amount"] or 0, bool(last_paid and d <= last_paid))
                    for _, d in schedule(st, m_["freq_value"], m_["freq_unit"]) if in_win(d)] if st else []
            outs.append(planned(f"mnt:{m_['id']}", m_["name"], dues, in_pays))

        # salaries: day rate x the days each person was checked in (Dashboard attendance) in the period
        emps = q("SELECT * FROM employees WHERE active=1 ORDER BY id")
        att = {}
        for x in q("SELECT employee_id, date FROM attendance"):
            d = parse_day(x["date"])
            if d and in_win(d):
                att.setdefault(x["employee_id"], []).append(d)
        pay_rows = [{"date": x["date"], "label": x["name"], "note": x["note"] or "", "amount": x["amount"]}
                    for x in q("""SELECT p.*, e.name FROM employee_payments p JOIN employees e ON e.id=p.employee_id
                                  WHERE p.kind='payroll'""") if parse_day(x["date"]) and in_win(parse_day(x["date"]))]
        paid_total = sum(x["amount"] for x in pay_rows)
        sal_dues = []
        for e in emps:
            days = att.get(e["id"], [])
            if days:
                amt = (e["per_day_rate"] or 0) * len(days)
                dd = max(days)
            elif not any(True for x in q("SELECT 1 FROM attendance WHERE employee_id=? LIMIT 1", e["id"])) and e["present_days"]:
                amt, dd = (e["per_day_rate"] or 0) * e["present_days"], today0      # days typed in Cash Flow (this cycle)
            else:
                continue
            if amt > 0:
                sal_dues.append((dd, amt, e["name"], len(days) or e["present_days"]))
        sal_total = sum(a for _, a, _, _ in sal_dues)
        if sal_total >= paid_total and sal_total > 0:
            left = paid_total
            rows = []
            for d, a, name, n in sorted(sal_dues, key=lambda x: x[2]):
                ok = left >= a - 0.01
                left -= a if ok else 0
                rows.append({"date": d.strftime("%Y-%m-%d"), "label": name, "note": f"{n:g} day(s) · {'paid' if ok else 'to pay'}",
                             "amount": a, "status": "paid" if ok else "to pay"})
            sl = slice_("payroll", "Salaries", "out", rows)
            if sl:
                sl.update({"due": round(sal_total, 2), "unpaid": round(sum(r["amount"] for r in rows if r["status"] != "paid"), 2),
                           "planned": True})
            outs.append(sl)
        else:
            outs.append(slice_("payroll", "Salaries", "out", [{**x, "status": "paid"} for x in pay_rows]))

        # monthly benefits / contributions (Cash Flow custom types: SSS, PhilHealth...), due once a month
        n_emp = len(emps)
        for c in q("SELECT c.*, t.label tl FROM custom_items c JOIN custom_types t ON t.id=c.custom_type_id ORDER BY c.id"):
            each = (c["amount"] or 0) * (n_emp if c["basis"] == "per_employee" else 1)
            dues = [(d, each, True) for d in months]          # no payment log for these: counted as spent each month
            outs.append(planned(f"cus:{c['id']}", f"{c['tl']}: {c['name']}" if c["tl"] != c["name"] else c["name"], dues, []))

        for kind, label in (("bonus", "Bonuses"),):
            outs.append(slice_(kind, label, "out", [
                {"date": x["date"], "label": x["name"], "note": x["note"] or "", "amount": x["amount"]}
                for x in q("""SELECT p.*, e.name FROM employee_payments p JOIN employees e ON e.id=p.employee_id
                              WHERE p.kind=? AND p.date>=?""", kind, start)]))
        outs.append(slice_("advances", "Cash advances", "out", [
            {"date": x["date"], "label": x["name"], "note": x["note"] or "", "amount": x["amount"]}
            for x in q("""SELECT a.*, e.name FROM employee_cash_advances a JOIN employees e ON e.id=a.employee_id
                          WHERE a.date>=?""", start)]))
        outs.append(slice_("stock", "Stock purchases", "out", [
            {"date": x["bought_at"][:10], "label": f"{x['name'] or 'Item'} × {x['qty']:g} {x['unit'] or ''}".strip(),
             "note": x["supplier"] or "", "amount": x["qty"] * x["unit_cost"]}
            for x in q("""SELECT p.*, i.name, i.unit FROM stock_purchases p LEFT JOIN items i ON i.id=p.item_id
                          WHERE substr(p.bought_at,1,10)>=?""", start)]))
        outs.append(slice_("cash-out", "Other cash out", "out", [
            {"date": x["date"], "label": x["note"] or "Cash out", "amount": x["amount"]}
            for x in q("SELECT * FROM cash_entries WHERE direction='out' AND date>=?", start)]))
        ins = sorted([x for x in ins if x], key=lambda x: -x["amount"])
        outs = sorted([x for x in outs if x], key=lambda x: -x["amount"])
        tin, tout = sum(x["amount"] for x in ins), sum(x["amount"] for x in outs)
        unpaid = sum(x.get("unpaid", 0) for x in outs)
        return {"period": period, "from": None if period == "all" else start, "to": end_d.isoformat(), "byDay": by_day,
                "outUnpaid": round(unpaid, 2), "outPaid": round(tout - unpaid, 2),
                "in": ins, "out": outs, "totalIn": round(tin, 2), "totalOut": round(tout, 2), "net": round(tin - tout, 2),
                "series": [{"key": k, **{kk: round(vv, 2) for kk, vv in v.items()}} for k, v in sorted(series.items())]}

    @r.get("/savings-plan")
    def savings_plan(user=Depends(admin), conn=Depends(get_db)):
        """'To save' (Progression, bottom): money to put aside EVERY MONTH so big costs do not pile up.
        1) costs due every few months (change oil P2,500 every 6 months = save P417 a month; by month 2 you should
           have P833 put aside), loans/bills paid less often than monthly too;
        2) the restock fund: of every sale, the ORIGINAL (cost) price must be kept to buy the item again
           (sell P150, cost P100 -> keep P100; no cost given = 65% of the selling price). If the cost price went up
           since, the extra comes out of the profit. Only items at their C / B / A stock level are listed: an item
           joins when it drops to C and leaves once it is bought again (above C). `auto` = the Auto inventory list."""
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        t = datetime.combine(datetime.now().date(), datetime.min.time())
        month0 = t.replace(day=1)
        rec = []

        def add_rec(kind, rid, name, amount, v, unit, dues):
            """dues: [(date, paid?)] in order"""
            nxt = next((d for d, ok in dues if not ok), None)
            n = months_in_cycle(v, unit)
            monthly = monthly_share(amount, v, unit)
            row = {"kind": kind, "id": rid, "name": name, "amount": round(amount, 2), "every": every(v, unit),
                   "months": n, "monthly": round(monthly, 2), "nextDue": nxt.strftime("%Y-%m-%d") if nxt else None,
                   "monthIndex": None, "shouldHave": None, "status": "no date"}
            if nxt:
                cs = add_period_back(nxt, v, unit)               # the cycle started at the previous due date
                if t >= nxt:
                    row.update(status="overdue", shouldHave=round(amount, 2), monthIndex=n)
                else:
                    idx = (t.year - cs.year) * 12 + t.month - cs.month + 1
                    idx = max(1, min(int(round(n)) if unit == "months" else int(n + 0.999), idx))
                    row.update(status="saving", monthIndex=idx, shouldHave=round(min(amount, monthly * idx), 2),
                               monthsLeft=max(0, (nxt.year - t.year) * 12 + nxt.month - t.month))
            rec.append(row)

        far = t + timedelta(days=4000)
        for m_ in q("SELECT * FROM maintenance_items ORDER BY id"):
            if not (m_["amount"] or 0) > 0 or not is_long(m_["freq_value"], m_["freq_unit"]):
                continue
            st = parse_day(m_["start_date"]) or parse_day(m_["last_paid"])
            lp = parse_day(m_["last_paid"])
            dues = []
            if st:
                d, k = st, 0
                while d <= far and k < 400:
                    dues.append((d, bool(lp and d <= lp)))
                    if not dues[-1][1]:
                        break
                    k += 1
                    d = add_period(st, m_["freq_value"] * k, "months") if m_["freq_unit"] == "months" else add_period(d, m_["freq_value"], m_["freq_unit"])
            add_rec("maintenance", m_["id"], m_["name"], m_["amount"], m_["freq_value"], m_["freq_unit"], dues)
        for o in q("SELECT * FROM obligations WHERE status='active' ORDER BY id"):
            st = parse_day(o["start_date"])
            if not (o["final_payment"] or 0) > 0 or not is_long(o["freq_value"], o["freq_unit"]) or not st:
                continue
            periods = obl_periods(o, st)
            paid_n = conn.execute("SELECT COUNT(*) FROM obligation_payments WHERE obligation_id=?", (o["id"],)).fetchone()[0]
            dues = [(st if k == 0 else add_period(st, o["freq_value"] * k, "months") if o["freq_unit"] == "months"
                     else st + timedelta(days=k * cycle_days(o["freq_value"], o["freq_unit"])), k < paid_n)
                    for k in range(paid_n + 1 if periods is None else min(periods, paid_n + 1))]
            add_rec("obligation", o["id"], o["name"], o["final_payment"] / (periods or 1), o["freq_value"], o["freq_unit"], dues)
        rec.sort(key=lambda x: (x["nextDue"] or "9999", x["name"]))

        auto = auto_inventory(conn, month0)
        restock = [x for x in auto["items"] if x["units"] > 0]          # money to keep: critical items that were sold
        cogs_month = auto["cogsThisMonth"]
        rmonthly = sum(x["monthly"] for x in rec)
        return {"month": month0.strftime("%Y-%m"), "recurring": rec, "recurringMonthly": round(rmonthly, 2),
                "recurringShouldHave": round(sum(x["shouldHave"] or 0 for x in rec), 2),
                "restock": restock, "restockSaved": round(sum(x["saved"] for x in restock), 2),
                "restockRebuy": round(sum(x["rebuy"] for x in restock), 2),
                "restockExtra": round(sum(x["extra"] for x in restock), 2),
                "restockProfit": round(sum(x["profit"] for x in restock), 2),
                "auto": auto["items"], "autoCost": auto["buyCost"], "autoCount": len(auto["items"]),
                "cogsThisMonth": round(cogs_month, 2), "saveThisMonth": round(rmonthly + cogs_month, 2)}

    def auto_inventory(conn, month0=None):
        """AUTO INVENTORY: every item whose stock is at its C, B or A level (A = critical; an item without levels, like
        pieces and rolls, only when it ran out). An item joins the list when it drops to C and leaves it by itself once
        the stock is above C again (bought / counted again). For each: what was sold since it was last bought (no
        purchase logged: since the 1st of the month), the money kept for it (that many x its cost) and how much to buy.
        The cost is the item's standard cost, or 65% of the selling price when the cost was never given."""
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        if month0 is None:
            month0 = datetime.combine(datetime.now().date(), datetime.min.time()).replace(day=1)
        m0 = month0.strftime("%Y-%m-%d")
        last_buy = {r_[0]: r_[1] for r_ in q("SELECT item_id, MAX(bought_at) FROM stock_purchases GROUP BY item_id")}
        rows = q("""SELECT si.item_id, si.qty, si.sell_price, si.cost_price, replace(s.created,' ','T') created
                    FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE si.qty > 0""")
        # what the bulk orders (Add Order) took out of Supply counts like sold goods: it has to be bought again too
        rows += q("""SELECT oi.item_id, oi.stock_taken qty, oi.price sell_price, i.cost_price, replace(o.created,' ','T') created
                     FROM bulk_order_items oi JOIN bulk_orders o ON o.id=oi.order_id JOIN items i ON i.id=oi.item_id
                     WHERE COALESCE(oi.stock_taken,0) > 0""")
        agg, cogs_month = {}, 0.0
        for x in rows:
            cost = shop_db.cost_of(x["cost_price"], x["sell_price"])
            if (x["created"] or "")[:10] >= m0:
                cogs_month += x["qty"] * cost
            since = (last_buy.get(x["item_id"]) or m0).replace(" ", "T")
            if (x["created"] or "") <= since:
                continue
            a = agg.setdefault(x["item_id"], {"units": 0.0, "saved": 0.0, "sales": 0.0})
            a["units"] += x["qty"]
            a["saved"] += x["qty"] * cost
            a["sales"] += x["qty"] * (x["sell_price"] or 0)
        out = []
        for i in q("SELECT * FROM items WHERE COALESCE(qty_unknown,0)=0 AND deleted_at IS NULL ORDER BY name"):  # N/A: count first
            level = shop_db.stock_level(i["quantity"], i["level_a"], i["level_b"], i["level_c"])
            if not level:
                continue
            a = agg.get(i["id"], {"units": 0.0, "saved": 0.0, "sales": 0.0})
            cost_now = shop_db.cost_of(i["cost_price"], i["sell_price"])
            tops = [v for v in (i["level_a"], i["level_b"], i["level_c"]) if v is not None]
            above = (max(tops) - (i["quantity"] or 0) + 1) if tops else 0      # to be above the highest level again
            buy = max(a["units"], above, 0)
            if buy > 0 and abs(buy - round(buy)) < 1e-9:
                buy = round(buy)
            rebuy = a["units"] * cost_now
            lb = last_buy.get(i["id"])
            out.append({"id": i["id"], "name": i["name"], "unit": i["unit"], "qty": i["quantity"], "level": level,
                        "levelA": i["level_a"], "levelB": i["level_b"], "levelC": i["level_c"],
                        "units": round(a["units"], 3), "since": lb[:10] if lb else m0, "sinceKind": "bought" if lb else "month",
                        "saved": round(a["saved"], 2), "rebuy": round(rebuy, 2), "extra": round(rebuy - a["saved"], 2),
                        "profit": round(a["sales"] - a["saved"], 2), "costNow": cost_now, "costAuto": bool(i["cost_auto"]),
                        "sellPrice": i["sell_price"], "buy": buy or None, "buyCost": round((buy or 0) * cost_now, 2)})
        rank = {"A": 0, "B": 1, "C": 2}
        out.sort(key=lambda x: (rank[x["level"]], -x["saved"], x["name"].lower()))
        return {"items": out, "buyCost": round(sum(x["buyCost"] for x in out), 2), "cogsThisMonth": round(cogs_month, 2)}

    @r.get("/auto-inventory")
    def auto_inventory_route(user=Depends(admin), conn=Depends(get_db)):
        return auto_inventory(conn)

    @r.post("/cash")
    def add_cash(body: CashIn, user=Depends(admin), conn=Depends(get_db)):
        if body.direction not in ("in", "out") or body.amount <= 0:
            raise HTTPException(422, "direction is in or out and amount must be above 0")
        with conn:
            conn.execute("INSERT INTO cash_entries(date,direction,amount,note) VALUES(?,?,?,?)",
                         (today(), body.direction, body.amount, body.note))
        note(conn, user, "Cash " + body.direction, f"{peso_txt(body.amount)}" + (f" - {body.note}" if body.note else ""))
        return cash(user, conn)

    # ---------------- supply ----------------
    def item_name(product, spec):
        return f"{product} {spec}".strip() if spec else product

    def ensure_products(conn):
        orphans = conn.execute("SELECT id, name FROM items WHERE product_id IS NULL AND deleted_at IS NULL").fetchall()
        if not orphans:
            return
        with conn:
            for it in orphans:
                conn.execute("INSERT OR IGNORE INTO products(name, created) VALUES(?,?)", (it["name"], now()))
                pid = conn.execute("SELECT id FROM products WHERE name=?", (it["name"],)).fetchone()[0]
                conn.execute("UPDATE items SET product_id=? WHERE id=?", (pid, it["id"]))

    def type_dict(i, conn=None):
        unknown = bool(i["qty_unknown"])            # N/A: the stock was not counted yet
        d = {"id": i["id"], "spec": i["spec"] or "", "unit": i["unit"], "qty": i["quantity"], "qtyUnknown": unknown,
             "sellPrice": i["sell_price"], "costPrice": i["cost_price"], "costAuto": bool(i["cost_auto"]),
             "levelA": i["level_a"], "levelB": i["level_b"], "levelC": i["level_c"],
             "level": None if unknown else shop_db.stock_level(i["quantity"], i["level_a"], i["level_b"], i["level_c"])}
        if conn is not None:     # extra profit from buying below the standard cost (negative = paid more)
            d["savings"] = conn.execute("SELECT COALESCE(SUM(savings),0) FROM stock_purchases WHERE item_id=?",
                                        (i["id"],)).fetchone()[0]
        return d

    def product_dict(conn, p):
        items = conn.execute("SELECT * FROM items WHERE product_id=? AND deleted_at IS NULL ORDER BY id", (p["id"],)).fetchall()
        img = PRODUCT_DIR / p["image"] if p["image"] else None
        has = bool(img and img.exists())
        return {"id": p["id"], "name": p["name"], "hasImage": has, "imageVer": int(img.stat().st_mtime) if has else 0,
                "types": [type_dict(i, conn) for i in items]}

    @r.get("/supply")
    def supply(user=Depends(admin), conn=Depends(get_db)):
        ensure_products(conn)
        return [product_dict(conn, p) for p in conn.execute("SELECT * FROM products ORDER BY name")]

    class TypeIn(BaseModel):
        spec: str = ""
        unit: str = "pc"
        qty: float = 0
        qtyUnknown: bool = False        # "N/A (not sure yet)": the stock gets counted later (Priority keeps asking)
        sellPrice: float = 0
        costPrice: float = 0
        levelA: Optional[float] = None
        levelB: Optional[float] = None
        levelC: Optional[float] = None

    class ProductIn(BaseModel):
        name: str
        iconKey: Optional[str] = None
        types: list[TypeIn] = []

    def insert_type(conn, product, t):
        auto = not (t.costPrice or 0) > 0                  # no cost typed: 65% of the selling price
        return conn.execute("""INSERT INTO items(name,unit,quantity,sell_price,cost_price,product_id,spec,level_a,level_b,level_c,
                                                 cost_auto,qty_unknown) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (item_name(product["name"], t.spec.strip()), t.unit or "pc", 0 if t.qtyUnknown else t.qty,
                             t.sellPrice, shop_db.assumed_cost(t.sellPrice) if auto else t.costPrice,
                             product["id"], t.spec.strip() or None, t.levelA, t.levelB, t.levelC, 1 if auto else 0,
                             1 if t.qtyUnknown else 0)).lastrowid

    @r.post("/supply/products")
    def add_product(body: ProductIn, user=Depends(admin), conn=Depends(get_db)):
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "name is required")
        try:
            with conn:
                cur = conn.execute("INSERT INTO products(name, icon_key, created) VALUES(?,?,?)", (name, body.iconKey, now()))
                p = {"id": cur.lastrowid, "name": name}
                for t in body.types:
                    insert_type(conn, p, t)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A product or variant with that name already exists")
        note(conn, user, "New item", f"{name} ({len(body.types)} type(s))")
        return product_dict(conn, conn.execute("SELECT * FROM products WHERE id=?", (p["id"],)).fetchone())

    class ProductPatch(BaseModel):
        name: Optional[str] = None
        iconKey: Optional[str] = None

    @r.put("/supply/products/{pid}")
    def edit_product(pid: int, body: ProductPatch, user=Depends(admin), conn=Depends(get_db)):
        p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Product not found")
        name = (body.name or p["name"]).strip()
        try:
            with conn:
                conn.execute("UPDATE products SET name=?, icon_key=? WHERE id=?", (name, body.iconKey if body.iconKey is not None else p["icon_key"], pid))
                for i in conn.execute("SELECT id, spec FROM items WHERE product_id=?", (pid,)).fetchall():
                    conn.execute("UPDATE items SET name=? WHERE id=?", (item_name(name, i["spec"]), i["id"]))  # OCR matches by name
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That name is already used")
        return product_dict(conn, conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())

    # ---- deleting a Supply item (round 17: also when it was sold before) ----
    def item_history(conn, iid):
        """how many records point to this item: sales, receipt rows, stock purchases, bulk-order rows"""
        return conn.execute("""SELECT (SELECT COUNT(*) FROM sale_items WHERE item_id=?)
                                    + (SELECT COUNT(*) FROM receipt_lines WHERE item_id=?)
                                    + (SELECT COUNT(*) FROM stock_purchases WHERE item_id=?)
                                    + (SELECT COUNT(*) FROM bulk_order_items WHERE item_id=?)""", (iid, iid, iid, iid)).fetchone()[0]

    def free_item_name(conn, base):
        name, n = base, 2
        while conn.execute("SELECT 1 FROM items WHERE name=?", (name,)).fetchone():
            name, n = f"{base} {n}", n + 1
        return name

    def drop_item(conn, iid):
        """Take one item (variant) out of Supply, inside the caller's transaction. Without any history it is really
        deleted. With history (sold, on receipts, bought, on an order) the row stays for the books as
        '<name> (deleted)': gone from Supply, the phones, the receipt reader, the stock lists and the Priority cards.
        Rows of it that were NOT sold yet go back to 'not in Supply' on their receipt; open orders keep the row as text.
        Returns 'deleted' | 'kept'."""
        it = conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        if it is None:
            return "deleted"
        kept = item_history(conn, iid) > 0
        for l in conn.execute("SELECT * FROM receipt_lines WHERE item_id=? AND sold=0", (iid,)).fetchall():
            conn.execute("""INSERT INTO receipt_unmatched(receipt_id, ocr_text, item_text, qty, price, unit)
                            VALUES(?,?,?,?,?,?)""", (l["receipt_id"], l["ocr_text"], l["item_text"] or it["name"], l["qty"],
                                                     l["price"], it["unit"]))
            conn.execute("DELETE FROM receipt_lines WHERE id=?", (l["id"],))
        conn.execute("""UPDATE bulk_order_items SET item_id=NULL WHERE item_id=?
                        AND order_id IN (SELECT id FROM bulk_orders WHERE status='active')""", (iid,))
        conn.execute("DELETE FROM supply_sourcing WHERE item_id=?", (iid,))
        if kept:
            conn.execute("""UPDATE items SET deleted_at=?, name=?, product_id=NULL, qty_unknown=0,
                            level_a=NULL, level_b=NULL, level_c=NULL WHERE id=?""",
                         (now(), free_item_name(conn, f"{it['name']} (deleted)"), iid))
            return "kept"
        conn.execute("DELETE FROM stock_purchases WHERE item_id=?", (iid,))
        conn.execute("DELETE FROM items WHERE id=?", (iid,))
        return "deleted"

    @r.delete("/supply/products/{pid}")
    def delete_product(pid: int, user=Depends(admin), conn=Depends(get_db)):
        """Delete an item and all its types. Types that were sold / bought before stay in the books as
        '<name> (deleted)' (past sales keep their name); everything else about them is gone."""
        prow = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not prow:
            raise HTTPException(404, "Product not found")
        with conn:
            ids = [i["id"] for i in conn.execute("SELECT id FROM items WHERE product_id=? AND deleted_at IS NULL", (pid,))]
            kept = sum(1 for iid in ids if drop_item(conn, iid) == "kept")
            conn.execute("UPDATE items SET product_id=NULL WHERE product_id=?", (pid,))
            conn.execute("DELETE FROM products WHERE id=?", (pid,))
        if prow["image"]:
            (PRODUCT_DIR / prow["image"]).unlink(missing_ok=True)
        note(conn, user, "Deleted item", prow["name"] + (" (its past sales stay in the books)" if kept else ""))
        return {"deleted": pid, "keptHistory": kept}

    @r.post("/supply/products/{pid}/types")
    def add_type(pid: int, body: TypeIn, user=Depends(admin), conn=Depends(get_db)):
        p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Product not found")
        try:
            with conn:
                insert_type(conn, p, body)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That variant already exists")
        note(conn, user, "New type", f"{p['name']} {body.spec}".strip())
        return product_dict(conn, p)

    class ItemPatch(BaseModel):
        spec: Optional[str] = None
        unit: Optional[str] = None
        sellPrice: Optional[float] = None
        costPrice: Optional[float] = None
        levelA: Optional[float] = None          # send null to clear a level
        levelB: Optional[float] = None
        levelC: Optional[float] = None

    @r.patch("/supply/items/{iid}")
    def edit_item(iid: int, body: ItemPatch, user=Depends(admin), conn=Depends(get_db)):
        i = conn.execute("""SELECT i.*, p.name AS pname FROM items i LEFT JOIN products p ON p.id=i.product_id
                            WHERE i.id=? AND i.deleted_at IS NULL""", (iid,)).fetchone()
        if not i:
            raise HTTPException(404, "Item not found")
        spec = body.spec.strip() if body.spec is not None else (i["spec"] or "")
        sell = body.sellPrice if body.sellPrice is not None else i["sell_price"]
        # the cost: typed = the real cost from now on; 0 / empty = 65% of the selling price; an assumed cost sent back
        # unchanged follows the (new) selling price
        cost, auto = i["cost_price"], i["cost_auto"]
        if body.costPrice is not None:
            if not body.costPrice > 0 or (auto and abs(body.costPrice - (i["cost_price"] or 0)) < 0.005):
                cost, auto = shop_db.assumed_cost(sell), 1
            else:
                cost, auto = body.costPrice, 0
        elif auto:
            cost = shop_db.assumed_cost(sell)
        try:
            with conn:
                conn.execute("UPDATE items SET spec=?, name=?, unit=?, sell_price=?, cost_price=?, cost_auto=? WHERE id=?",
                             (spec or None, item_name(i["pname"] or i["name"], spec), body.unit or i["unit"],
                              sell, cost, auto, iid))
                sent = fields_set(body)
                for f, col in (("levelA", "level_a"), ("levelB", "level_b"), ("levelC", "level_c")):
                    if f in sent:
                        conn.execute(f"UPDATE items SET {col}=? WHERE id=?", (getattr(body, f), iid))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That variant already exists")
        sent = fields_set(body)
        parts = []
        if "sellPrice" in sent and body.sellPrice != i["sell_price"]:
            parts.append(f"sell {peso_txt(i['sell_price'])} → {peso_txt(body.sellPrice)}")
        if abs((cost or 0) - (i["cost_price"] or 0)) >= 0.005:
            parts.append(f"standard cost {peso_txt(i['cost_price'])} → {peso_txt(cost)}" + (" (65% of the selling price)" if auto else ""))
        lv = [f"{f[-1]} {getattr(body, f):g}" if getattr(body, f) is not None else f"{f[-1]} off" for f in ("levelA", "levelB", "levelC") if f in sent]
        if lv:
            parts.append("levels " + ", ".join(lv))
        if parts:
            note(conn, user, "Supply changed", f"{i['name']}: " + "; ".join(parts))
        return type_dict(conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone(), conn)

    class AdjustIn(BaseModel):
        delta: float
        note: Optional[str] = None

    class CountIn(BaseModel):
        qty: float

    @r.post("/supply/items/{iid}/count")
    def count_stock(iid: int, body: CountIn, user=Depends(admin), conn=Depends(get_db)):
        """The stock was counted: the quantity IS this number from now on (an item added with quantity N/A leaves the
        dashboard Priority with it)."""
        if body.qty < 0:
            raise HTTPException(422, "The count cannot be below 0")
        i = conn.execute("SELECT * FROM items WHERE id=? AND deleted_at IS NULL", (iid,)).fetchone()
        if not i:
            raise HTTPException(404, "Item not found")
        with conn:
            conn.execute("UPDATE items SET quantity=?, qty_unknown=0 WHERE id=?", (body.qty, iid))
        note(conn, user, "Stock counted", f"{i['name']}: {body.qty:g} {i['unit']}"
                                          + (" (was N/A)" if i["qty_unknown"] else f" (was {i['quantity']:g})"))
        return type_dict(conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone(), conn)

    @r.post("/supply/items/{iid}/adjust")
    def adjust_stock(iid: int, body: AdjustIn, user=Depends(admin), conn=Depends(get_db)):
        """Manual stock CORRECTION (count was wrong, damaged goods). Buying stock must use /purchase so cost history is kept.
        (A correction means the stock was counted: an N/A item gets a real quantity.)"""
        with conn:
            n = conn.execute("""UPDATE items SET quantity = quantity + ?, qty_unknown=0 WHERE id=? AND quantity + ? >= 0
                                AND deleted_at IS NULL""", (body.delta, iid, body.delta)).rowcount
        if not n:
            raise HTTPException(409, "Item not found or stock would go below 0")
        nm, unit = iname(conn, iid)
        note(conn, user, "Stock corrected", f"{nm}: {body.delta:+g} {unit}" + (f" ({body.note})" if body.note else ""))
        return type_dict(conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone(), conn)

    # ---- product photo (replaces the old icon) ----
    def save_jpeg(upload, dest, box):
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(upload.file.read()))).convert("RGB")
        except Exception:
            raise HTTPException(422, "That file is not an image")
        im.thumbnail(box)
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "JPEG", quality=86)

    @r.post("/supply/products/{pid}/image")
    def product_image_upload(pid: int, image: UploadFile = File(...), user=Depends(admin), conn=Depends(get_db)):
        p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Product not found")
        save_jpeg(image, PRODUCT_DIR / f"{pid}.jpg", (600, 600))
        with conn:
            conn.execute("UPDATE products SET image=? WHERE id=?", (f"{pid}.jpg", pid))
        note(conn, user, "Photo", f"item photo uploaded ({p['name']})")
        return product_dict(conn, conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())

    @r.delete("/supply/products/{pid}/image")
    def product_image_delete(pid: int, user=Depends(admin), conn=Depends(get_db)):
        p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Product not found")
        if p["image"]:
            (PRODUCT_DIR / p["image"]).unlink(missing_ok=True)
        with conn:
            conn.execute("UPDATE products SET image=NULL WHERE id=?", (pid,))
        return product_dict(conn, conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())

    @r.get("/supply/products/{pid}/image")
    def product_image(pid: int, user=Depends(admin_q), conn=Depends(get_db)):
        p = conn.execute("SELECT image FROM products WHERE id=?", (pid,)).fetchone()
        if not p or not p["image"] or not (PRODUCT_DIR / p["image"]).exists():
            raise HTTPException(404, "No photo")
        return FileResponse(PRODUCT_DIR / p["image"], media_type="image/jpeg")

    @r.delete("/supply/items/{iid}")
    def delete_item(iid: int, user=Depends(admin), conn=Depends(get_db)):
        """Delete one type (variant). Sold / bought before: it stays in the books as '<name> (deleted)'."""
        if not conn.execute("SELECT 1 FROM items WHERE id=? AND deleted_at IS NULL", (iid,)).fetchone():
            raise HTTPException(404, "Item not found")
        nm, _ = iname(conn, iid)
        with conn:
            how = drop_item(conn, iid)
        note(conn, user, "Deleted type", nm + (" (its past sales stay in the books)" if how == "kept" else ""))
        return {"deleted": iid, "keptHistory": 1 if how == "kept" else 0}

    class PurchaseIn(BaseModel):
        qty: float
        unitCost: float
        source: str = "external"
        supplier: Optional[str] = None
        note: Optional[str] = None

    @r.post("/supply/items/{iid}/purchase")
    def purchase(iid: int, body: PurchaseIn, user=Depends(admin), conn=Depends(get_db)):
        if body.qty <= 0 or body.source not in ("internal", "external"):
            raise HTTPException(422, "qty must be above 0; source is internal or external")
        if not conn.execute("SELECT 1 FROM items WHERE id=? AND deleted_at IS NULL", (iid,)).fetchone():
            raise HTTPException(404, "Item not found")
        try:
            pid = shop_db.record_purchase(conn, iid, body.qty, body.unitCost, body.source, body.supplier, None, body.note)
        except ValueError as e:
            raise HTTPException(404, str(e))
        row = conn.execute("SELECT * FROM stock_purchases WHERE id=?", (pid,)).fetchone()
        nm, unit = iname(conn, iid)
        note(conn, user, "Stock bought", f"{nm}: +{body.qty:g} {unit} at {peso_txt(body.unitCost)} "
             f"({'saved' if row['savings'] >= 0 else 'over standard by'} {peso_txt(abs(row['savings']))})")
        return {"purchase": dict(row), "item": type_dict(conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone(), conn)}

    @r.get("/supply/items/{iid}/purchases")
    def purchases(iid: int, user=Depends(admin), conn=Depends(get_db)):
        rows = [dict(x) for x in conn.execute("SELECT * FROM stock_purchases WHERE item_id=? ORDER BY id DESC", (iid,))]
        return {"purchases": rows, "totalSavings": sum(x["savings"] for x in rows)}

    class SourceIn(BaseModel):
        unitCost: float = 0
        transferCost: float = 0
        maintenanceCost: float = 0

    class SourcingIn(BaseModel):
        internal: Optional[SourceIn] = None
        external: Optional[SourceIn] = None

    def sourcing_payload(conn, iid):
        out = {"internal": None, "external": None}
        for x in conn.execute("SELECT * FROM supply_sourcing WHERE item_id=?", (iid,)):
            out[x["source"]] = {"unitCost": x["unit_cost"], "transferCost": x["transfer_cost"],
                                "maintenanceCost": x["maintenance_cost"],
                                "landed": x["unit_cost"] + x["transfer_cost"] + x["maintenance_cost"]}
        a, b = out["internal"], out["external"]
        out["cheaper"] = (None if not (a and b) else "internal" if a["landed"] < b["landed"] else "external" if b["landed"] < a["landed"] else None)
        return out

    @r.get("/supply/items/{iid}/sourcing")
    def get_sourcing(iid: int, user=Depends(admin), conn=Depends(get_db)):
        return sourcing_payload(conn, iid)

    @r.put("/supply/items/{iid}/sourcing")
    def set_sourcing(iid: int, body: SourcingIn, user=Depends(admin), conn=Depends(get_db)):
        if not conn.execute("SELECT 1 FROM items WHERE id=? AND deleted_at IS NULL", (iid,)).fetchone():
            raise HTTPException(404, "Item not found")
        with conn:
            for src in ("internal", "external"):
                v = getattr(body, src)
                if v is not None:
                    conn.execute("""INSERT INTO supply_sourcing(item_id,source,unit_cost,transfer_cost,maintenance_cost,updated)
                                    VALUES(?,?,?,?,?,?) ON CONFLICT(item_id,source) DO UPDATE SET
                                    unit_cost=excluded.unit_cost, transfer_cost=excluded.transfer_cost,
                                    maintenance_cost=excluded.maintenance_cost, updated=excluded.updated""",
                                 (iid, src, v.unitCost, v.transferCost, v.maintenanceCost, now()))
        return sourcing_payload(conn, iid)

    # ================= CALENDAR: who was in charge, what was sold, who was present (per day) =================
    def check_day(day):
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "Use a date like 2026-10-15")
        return day

    hm = lambda iso: (iso or "")[11:16]

    @r.get("/calendar")
    def calendar(start: str, end: str, user=Depends(admin), conn=Depends(get_db)):
        """One summary per day between start and end (YYYY-MM-DD): sales, who was in charge, staff present, actions."""
        check_day(start), check_day(end)
        days = {}
        def day(x):
            return days.setdefault(x, {"date": x, "sales": 0, "nSales": 0, "inCharge": [], "present": 0, "actions": 0})
        for x in conn.execute("""SELECT substr(created,1,10) d, SUM(total) t, COUNT(*) n FROM sales
                                 WHERE substr(created,1,10) BETWEEN ? AND ? GROUP BY d""", (start, end)):
            day(x["d"]).update({"sales": x["t"], "nSales": x["n"]})
        for x in conn.execute("""SELECT a.date, COALESCE(u.display_name, u.username) name FROM user_activity a
                                 JOIN users u ON u.id=a.user_id WHERE u.is_admin=1 AND a.date BETWEEN ? AND ?
                                 ORDER BY a.first_seen""", (start, end)):
            day(x["date"])["inCharge"].append(x["name"])
        for x in conn.execute("SELECT date, COUNT(*) n FROM attendance WHERE date BETWEEN ? AND ? GROUP BY date", (start, end)):
            day(x["date"])["present"] = x["n"]
        for x in conn.execute("""SELECT substr(at,1,10) d, COUNT(*) n FROM audit_log WHERE substr(at,1,10) BETWEEN ? AND ?
                                 GROUP BY d""", (start, end)):
            day(x["d"])["actions"] = x["n"]
        first = conn.execute("""SELECT MIN(d) FROM (SELECT MIN(substr(created,1,10)) d FROM sales UNION ALL
                                SELECT MIN(date) FROM user_activity UNION ALL SELECT MIN(date) FROM attendance UNION ALL
                                SELECT MIN(substr(at,1,10)) FROM audit_log)""").fetchone()[0]
        return {"days": sorted(days.values(), key=lambda d: d["date"]), "firstDate": first}

    def day_report(conn, d):
        people = lambda admin_flag: [
            {"name": x["name"], "username": x["username"], "uid": x["uid"], "first": hm(x["first_seen"]), "last": hm(x["last_seen"])}
            for x in conn.execute("""SELECT COALESCE(u.display_name,u.username) name, u.username, u.id uid, a.first_seen, a.last_seen
                                     FROM user_activity a JOIN users u ON u.id=a.user_id WHERE a.date=? AND u.is_admin=?
                                     ORDER BY a.first_seen""", (d, admin_flag))]
        sales, deducted = [], {}
        for sa in conn.execute("""SELECT s.*, COALESCE(u.display_name, u.username, 'Desktop') person FROM sales s
                                  LEFT JOIN users u ON u.id=s.user_id WHERE substr(s.created,1,10)=? ORDER BY s.id""", (d,)).fetchall():
            items = []
            for it in conn.execute("""SELECT si.*, i.name, i.unit FROM sale_items si JOIN items i ON i.id=si.item_id
                                      WHERE si.sale_id=? ORDER BY si.id""", (sa["id"],)):
                items.append({"item": it["name"], "unit": it["unit"], "qty": it["qty"], "price": it["sell_price"],
                              "amount": it["qty"] * it["sell_price"]})
                g = deducted.setdefault(it["item_id"], {"item": it["name"], "unit": it["unit"], "qty": 0, "amount": 0})
                g["qty"] += it["qty"]
                g["amount"] += it["qty"] * it["sell_price"]
            sales.append({"id": sa["id"], "time": hm(sa["created"]), "person": sa["person"], "total": sa["total"],
                          "profit": sa["profit"], "items": items})
        present = [{"name": x["name"], "position": x["position"] or "", "timeIn": x["time_in"]}
                   for x in conn.execute("""SELECT e.name, e.position, a.time_in FROM attendance a JOIN employees e
                                            ON e.id=a.employee_id WHERE a.date=? ORDER BY a.time_in""", (d,))]
        purchases = [{"time": hm(x["bought_at"]), "item": x["name"], "unit": x["unit"], "qty": x["qty"], "unitCost": x["unit_cost"],
                      "standardCost": x["standard_cost"], "savings": x["savings"], "source": x["source"]}
                     for x in conn.execute("""SELECT p.*, i.name, i.unit FROM stock_purchases p JOIN items i ON i.id=p.item_id
                                              WHERE substr(p.bought_at,1,10)=? ORDER BY p.id""", (d,))]
        log = [{"time": hm(x["at"]), "user": x["name"] or "system", "action": x["action"], "detail": x["detail"] or ""}
               for x in conn.execute("""SELECT a.*, COALESCE(u.display_name,u.username) name FROM audit_log a
                                        LEFT JOIN users u ON u.id=a.user_id WHERE substr(a.at,1,10)=? ORDER BY a.id""", (d,))]
        one = lambda sql: conn.execute(sql, (d,)).fetchone()[0] or 0
        money_in = {"sales": sum(x["total"] for x in sales),
                    "bulkOrders": one("SELECT SUM(amount) FROM bulk_order_payments WHERE date=?"),
                    "cashIn": one("SELECT SUM(amount) FROM cash_entries WHERE direction='in' AND date=?")}
        money_out = {"payroll": one("SELECT SUM(amount) FROM employee_payments WHERE date=?"),
                     "cashAdvances": one("SELECT SUM(amount) FROM employee_cash_advances WHERE date=?"),
                     "loansPayments": one("SELECT SUM(amount) FROM obligation_payments WHERE date=?"),
                     "maintenance": one("SELECT SUM(amount) FROM maintenance_payments WHERE date=?"),
                     "stockBought": sum(x["qty"] * x["unitCost"] for x in purchases),
                     "cashOut": one("SELECT SUM(amount) FROM cash_entries WHERE direction='out' AND date=?")}
        receipts = [{"id": x["id"], "label": shop_db.receipt_label(x["created"]), "person": x["person"], "status": x["status"],
                     "image": x["image"]}
                    for x in conn.execute("""SELECT r.*, COALESCE(u.display_name,u.username) person FROM receipts r
                                             LEFT JOIN users u ON u.id=r.user_id WHERE substr(r.created,1,10)=? ORDER BY r.id""", (d,))]
        deliveries = []
        for x in conn.execute("""SELECT r.*, o.customer, o.address, t.truck_name, t.plate FROM bulk_order_runs r
                                 LEFT JOIN bulk_orders o ON o.id=r.order_id LEFT JOIN transports t ON t.id=r.transport_id
                                 WHERE r.date=? AND (o.id IS NOT NULL OR r.order_id IS NULL)
                                 ORDER BY COALESCE(o.customer, r.place, '')""", (d,)).fetchall():
            rd = run_dict(conn, x)
            who = {"pickup": "Customer picks up", "other": rd["carrier"] or "Someone else"}.get(rd["mode"])
            deliveries.append({"customer": x["customer"] or rd["label"], "address": x["address"] or rd["place"] or "", "truck": who or x["truck_name"] or "",
                               "plate": "" if who else x["plate"] or "", "trips": x["trips"], "driver": rd["driverName"],
                               "helpers": [h["name"] for h in rd["helpers"]], "done": rd["done"], "mode": rd["mode"]})
        return {"date": d, "inCharge": people(1), "salesPeople": people(0), "sales": sales, "deliveries": deliveries,
                "deducted": sorted(deducted.values(), key=lambda g: -g["amount"]), "present": present, "purchases": purchases,
                "log": log, "moneyIn": money_in, "moneyOut": money_out,
                "receipts": [{k: v for k, v in x.items() if k != "image"} for x in receipts], "_receipts": receipts,
                "totals": {"sales": money_in["sales"], "profit": sum(x["profit"] or 0 for x in sales),
                           "in": sum(money_in.values()), "out": sum(money_out.values())}}

    @r.get("/calendar/day/{day}")
    def calendar_day(day: str, user=Depends(admin), conn=Depends(get_db)):
        rep_ = day_report(conn, check_day(day))
        rep_.pop("_receipts")
        return rep_

    def csv_bytes(header, rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return ("﻿" + buf.getvalue()).encode("utf-8")      # BOM so Excel shows ₱ and accents correctly

    def folder_name(person):
        """A sales person's name as a Windows folder name (no \\ / : * ? " < > |)."""
        name = _re_mod.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", person or "").strip(" .")
        return _re_mod.sub(r"\s+", " ", name) or "No name (phone)"

    def temp_zip():
        f = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        f.close()
        return f.name

    def send_zip(path, filename):
        return FileResponse(path, media_type="application/zip", filename=filename,
                            background=BackgroundTask(lambda: __import__("os").remove(path)))

    @r.get("/calendar/day/{day}/download")
    def calendar_day_download(day: str, user=Depends(admin_q), conn=Depends(get_db)):
        """ZIP of one day: summary.txt + CSV files (open in Excel) + that day's receipt photos."""
        R = day_report(conn, check_day(day))
        path = temp_zip()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            t = R["totals"]
            lines = [f"Tres Niños Hardware - {day}", "",
                     "IN CHARGE: " + (", ".join(f"{p['name']} ({p['first']}-{p['last']})" for p in R["inCharge"]) or "nobody signed in"),
                     "SALES PEOPLE ON THE PHONE: " + (", ".join(f"{p['name']} ({p['first']}-{p['last']})" for p in R["salesPeople"]) or "-"),
                     "PRESENT: " + (", ".join(f"{p['name']} {p['timeIn']}" for p in R["present"]) or "-"), "",
                     f"Sales: {peso_txt(t['sales'])} in {len(R['sales'])} sale(s), profit {peso_txt(t['profit'])}",
                     f"Money in: {peso_txt(t['in'])}   Money out: {peso_txt(t['out'])}", "", "STOCK DEDUCTED:"]
            lines += [f"  {g['item']}: {g['qty']:g} {g['unit']}  ({peso_txt(g['amount'])})" for g in R["deducted"]] or ["  -"]
            lines += ["", "DELIVERIES:"] + ([f"  {x['customer']}{' - ' + x['address'] if x['address'] else ''}: "
                                              f"{x['truck'] or 'no truck'} {x['plate']} x{x['trips']}"
                                              f" · driver {x['driver'] or '-'} · helpers {', '.join(x['helpers']) or '-'}"
                                              f"{' · DELIVERED' if x['done'] else ''}" for x in R["deliveries"]] or ["  -"])
            lines += ["", "ACTIVITY:"] + [f"  {x['time']} {x['user']}: {x['action']} {x['detail']}" for x in R["log"]]
            z.writestr("summary.txt", "\n".join(lines).encode("utf-8"))
            z.writestr("sales_items.csv", csv_bytes(["sale", "time", "sold by", "item", "qty", "unit", "price", "amount"],
                       [[s_["id"], s_["time"], s_["person"], i["item"], i["qty"], i["unit"], i["price"], i["amount"]]
                        for s_ in R["sales"] for i in s_["items"]]))
            z.writestr("stock_deducted.csv", csv_bytes(["item", "qty", "unit", "amount"],
                       [[g["item"], g["qty"], g["unit"], g["amount"]] for g in R["deducted"]]))
            z.writestr("in_charge.csv", csv_bytes(["name", "username", "role", "first seen", "last seen"],
                       [[p["name"], p["username"], "desktop", p["first"], p["last"]] for p in R["inCharge"]] +
                       [[p["name"], p["username"], "phone", p["first"], p["last"]] for p in R["salesPeople"]]))
            z.writestr("attendance.csv", csv_bytes(["name", "position", "time in"],
                       [[p["name"], p["position"], p["timeIn"]] for p in R["present"]]))
            z.writestr("deliveries.csv", csv_bytes(["customer", "address", "truck", "plate", "trips", "driver", "helpers", "delivered"],
                       [[x["customer"], x["address"], x["truck"], x["plate"], x["trips"], x["driver"], ", ".join(x["helpers"]),
                         "yes" if x["done"] else "no"] for x in R["deliveries"]]))
            z.writestr("stock_bought.csv", csv_bytes(["time", "item", "qty", "unit", "paid per unit", "standard", "savings", "source"],
                       [[x["time"], x["item"], x["qty"], x["unit"], x["unitCost"], x["standardCost"], x["savings"], x["source"]]
                        for x in R["purchases"]]))
            z.writestr("activity_log.csv", csv_bytes(["time", "user", "action", "detail"],
                       [[x["time"], x["user"], x["action"], x["detail"]] for x in R["log"]]))
            z.writestr("money.csv", csv_bytes(["direction", "what", "amount"],
                       [["in", k, v] for k, v in R["moneyIn"].items()] + [["out", k, v] for k, v in R["moneyOut"].items()]))
            for rc in R["_receipts"]:
                f = UPLOAD_DIR / f"{rc['id']}.jpg"
                f = f if f.exists() else (BASE / rc["image"] if rc["image"] else None)
                if f and f.exists() and f.is_file():
                    z.write(f, f"receipts/{folder_name(rc['person'])}/{rc['label']}_{rc['id']}.jpg")   # one folder per sales person
        note(conn, user, "Downloaded", f"day record {day}")
        return send_zip(path, f"TresNinos_{day}.zip")

    # ================= STORAGE: disk space, archive old data, free space =================
    def size_of(path):
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    def db_file(conn):
        from pathlib import Path
        return Path(conn.execute("PRAGMA database_list").fetchone()[2])

    def receipt_photo(rc):
        f = UPLOAD_DIR / f"{rc['id']}.jpg"
        return f if f.exists() else None

    @r.get("/storage")
    def storage(user=Depends(admin), conn=Depends(get_db)):
        du = shutil.disk_usage(BASE)
        dbp = db_file(conn)
        photos = sum(f.stat().st_size for f in UPLOAD_DIR.glob("*.jpg")) if UPLOAD_DIR.exists() else 0
        app = {"database": sum(size_of(dbp.with_name(dbp.name + x)) for x in ("", "-wal", "-shm")),
               "receiptPhotos": photos, "thumbnails": size_of(THUMB_DIR), "employeePhotos": size_of(AVATAR_DIR),
               "itemPhotos": size_of(PRODUCT_DIR)}
        months = {}
        for rc in conn.execute("SELECT id, created FROM receipts"):
            f = receipt_photo(rc)
            m = months.setdefault((rc["created"] or "")[:7], {"month": (rc["created"] or "")[:7], "receipts": 0, "photos": 0, "bytes": 0})
            m["receipts"] += 1
            if f:
                m["photos"] += 1
                m["bytes"] += f.stat().st_size
        return {"disk": {"total": du.total, "used": du.used, "free": du.free, "folder": str(BASE)},
                "app": app, "appTotal": sum(app.values()), "months": sorted(months.values(), key=lambda m: m["month"])}

    ARCHIVE_TABLES = [   # (file name, SQL with one "before" parameter)
        ("sales", "SELECT * FROM sales WHERE created < ?"),
        ("sale_items", "SELECT si.* FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.created < ?"),
        ("receipts", "SELECT * FROM receipts WHERE created < ?"),
        ("receipt_lines", "SELECT l.* FROM receipt_lines l JOIN receipts r ON r.id=l.receipt_id WHERE r.created < ?"),
        ("stock_bought", "SELECT * FROM stock_purchases WHERE bought_at < ?"),
        ("attendance", "SELECT a.*, e.name FROM attendance a LEFT JOIN employees e ON e.id=a.employee_id WHERE a.date < ?"),
        ("in_charge", "SELECT a.*, u.username, u.display_name FROM user_activity a LEFT JOIN users u ON u.id=a.user_id WHERE a.date < ?"),
        ("activity_log", "SELECT * FROM audit_log WHERE at < ?"),
        ("cash_entries", "SELECT * FROM cash_entries WHERE date < ?"),
        ("payroll_and_bonus", "SELECT * FROM employee_payments WHERE date < ?"),
        ("cash_advances", "SELECT * FROM employee_cash_advances WHERE date < ?"),
        ("loan_and_bill_payments", "SELECT * FROM obligation_payments WHERE date < ?"),
        ("maintenance_payments", "SELECT * FROM maintenance_payments WHERE date < ?"),
        ("bulk_order_payments", "SELECT * FROM bulk_order_payments WHERE date < ?"),
    ]

    @r.get("/archive")
    def archive(before: str, user=Depends(admin_q), conn=Depends(get_db)):
        """'Transfer' step: a ZIP with a full backup copy of the database, CSV files of everything dated before `before`,
        and the receipt photos older than that date. Save it on a USB drive / another PC, then free the space."""
        check_day(before)
        path = temp_zip()
        tmpdb = temp_zip().replace(".zip", ".db")
        try:
            dst = sqlite3.connect(tmpdb)
            conn.backup(dst)                                     # consistent copy even while the server runs
            dst.close()
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmpdb, "inventory_backup.db")
                for name, sql in ARCHIVE_TABLES:
                    cur = conn.execute(sql, (before,))
                    cols = [c[0] for c in cur.description]
                    z.writestr(f"csv/{name}.csv", csv_bytes(cols, [list(x) for x in cur.fetchall()]))
                n = 0
                for rc in conn.execute("""SELECT r.id, r.created, r.image, COALESCE(u.display_name, u.username) person FROM receipts r
                                          LEFT JOIN users u ON u.id=r.user_id WHERE r.created < ?""", (before,)).fetchall():
                    f = receipt_photo(rc)
                    if f:
                        z.write(f, f"receipt_photos/{rc['created'][:7]}/{folder_name(rc['person'])}/"
                                   f"{shop_db.receipt_label(rc['created'])}_{rc['id']}.jpg")
                        n += 1
                z.writestr("README.txt", (f"Tres Niños archive of everything before {before}, made {now()}.\n"
                                          f"inventory_backup.db = the WHOLE database at that moment (open with DB Browser for SQLite).\n"
                                          f"csv/ = records dated before {before} (open in Excel).\n"
                                          f"receipt_photos/ = {n} receipt photo(s) before {before}.\n").encode("utf-8"))
        finally:
            __import__("os").path.exists(tmpdb) and __import__("os").remove(tmpdb)
        note(conn, user, "Archive downloaded", f"everything before {before}")
        return send_zip(path, f"TresNinos_archive_before_{before}.zip")

    class CleanupIn(BaseModel):
        before: str

    @r.post("/archive/cleanup")
    def archive_cleanup(body: CleanupIn, user=Depends(admin), conn=Depends(get_db)):
        """Free space: delete receipt PHOTOS (and their thumbnails) of receipts made before `before` that are finished
        (sold or failed). Sales, stock and all records stay in the database - only the pictures go."""
        before = check_day(body.before)
        if before > datetime.now().strftime("%Y-%m-%d"):
            raise HTTPException(422, "Pick a date in the past")
        files, freed = 0, 0
        for rc in conn.execute("""SELECT r.id, r.status FROM receipts r WHERE r.created < ? AND r.status IN ('done','failed')
                                  AND NOT EXISTS (SELECT 1 FROM receipt_lines l WHERE l.receipt_id=r.id AND l.sold=0)""",
                               (before,)).fetchall():
            for f in [UPLOAD_DIR / f"{rc['id']}.jpg", *THUMB_DIR.glob(f"{rc['id']}_*.jpg")]:
                try:
                    if f.is_file():
                        freed += f.stat().st_size
                        f.unlink()
                        files += 1
                except OSError:
                    pass
        with conn:
            conn.execute("DELETE FROM sessions WHERE expires < ?", (now(),))
            shop_db.audit(conn, user["id"], "Freed space", f"deleted {files} old photo file(s), {freed / 1048576:.1f} MB (before {before})")
        return {"deletedFiles": files, "freedBytes": freed}

    # ================= SAVED DATA VIEWER (Calendar -> Open saved data) =================
    # After "Archive & free space" (or a day download) the ZIP lives on a USB drive / another PC. Open it here to read
    # any day again: who was present, each sales person's items, the orders. Only the database (small) is kept on
    # this laptop, in opened_data/; receipt photos stay in the ZIP. "Remove" deletes the copy again.
    VIEW_DIR = BASE / "opened_data"
    import json as _json
    import re as _re
    import uuid as _uuid

    def view_index():
        f = VIEW_DIR / "index.json"
        try:
            items = _json.loads(f.read_text("utf-8")) if f.exists() else []
        except ValueError:
            items = []
        return [x for x in items if (VIEW_DIR / x["file"]).exists()]

    def save_view_index(items):
        VIEW_DIR.mkdir(parents=True, exist_ok=True)
        (VIEW_DIR / "index.json").write_text(_json.dumps(items, indent=1), "utf-8")

    def ro_db(path):
        c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def has_table(c, name):
        return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    def db_range(path):
        c = ro_db(path)
        try:
            if not (has_table(c, "sales") and has_table(c, "items")):
                raise HTTPException(422, "That database is not a Tres Niños shop database")
            parts = ["SELECT MIN(substr(created,1,10)) a, MAX(substr(created,1,10)) b FROM sales"]
            for t, col in (("attendance", "date"), ("bulk_order_payments", "date"), ("audit_log", "substr(at,1,10)")):
                if has_table(c, t):
                    parts.append(f"SELECT MIN({col}), MAX({col}) FROM {t}")
            rows = c.execute(" UNION ALL ".join(parts)).fetchall()
            lo = [x[0] for x in rows if x[0]]
            hi = [x[1] for x in rows if x[1]]
            return (min(lo) if lo else None), (max(hi) if hi else None)
        finally:
            c.close()

    def read_day_zip(z, filename):
        """a day download (TresNinos_YYYY-MM-DD.zip): CSV files of one day"""
        m = _re.search(r"(\d{4}-\d{2}-\d{2})", filename or "")
        summary = z.read("summary.txt").decode("utf-8", "replace") if "summary.txt" in z.namelist() else ""
        if not m:
            m = _re.search(r"(\d{4}-\d{2}-\d{2})", summary[:200])
        if not m:
            raise HTTPException(422, "Could not find the date of this day file")
        rows = lambda name: list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig")))) if name in z.namelist() else []
        num = lambda v: float(v or 0) if str(v or "").replace(".", "", 1).replace("-", "", 1).isdigit() else 0.0
        sales = {}
        for x in rows("sales_items.csv"):
            p_ = sales.setdefault(x.get("sold by") or "Desktop", {"person": x.get("sold by") or "Desktop", "items": {}, "sales": set()})
            p_["sales"].add(x.get("sale"))
            it = p_["items"].setdefault((x.get("item"), x.get("unit")), {"item": x.get("item"), "unit": x.get("unit"), "qty": 0, "amount": 0})
            it["qty"] += num(x.get("qty"))
            it["amount"] += num(x.get("amount"))
        people = [{"person": v["person"], "count": len(v["sales"]), "items": sorted(v["items"].values(), key=lambda i: -i["amount"]),
                   "total": round(sum(i["amount"] for i in v["items"].values()), 2)} for v in sales.values()]
        money = {x.get("what"): num(x.get("amount")) for x in rows("money.csv")}
        deliveries = [{"customer": x.get("customer") or "", "address": x.get("address") or "", "truck": x.get("truck") or "",
                       "plate": x.get("plate") or "", "trips": int(num(x.get("trips")) or 1), "driver": x.get("driver") or "",
                       "helpers": [h.strip() for h in (x.get("helpers") or "").split(",") if h.strip()],
                       "done": (x.get("delivered") or "").strip().lower() == "yes"} for x in rows("deliveries.csv")]
        return {"date": m.group(1), "present": [{"name": x.get("name"), "position": x.get("position") or "",
                                                  "timeIn": x.get("time in") or "", "ot": 0, "ut": 0} for x in rows("attendance.csv")],
                "salesByPerson": sorted(people, key=lambda p: -p["total"]), "orders": [], "deliveries": deliveries,
                "orderMoney": money.get("bulkOrders", 0), "summary": summary}

    @r.post("/archive/open")
    def archive_open(file: UploadFile = File(...), user=Depends(admin), conn=Depends(get_db)):
        """Open a saved ZIP (archive or one day) or a .db backup so it can be read in the Calendar."""
        VIEW_DIR.mkdir(parents=True, exist_ok=True)
        tmp = VIEW_DIR / f"upload_{_uuid.uuid4().hex}.tmp"
        try:
            with open(tmp, "wb") as out:
                shutil.copyfileobj(file.file, out, 1024 * 1024)
            vid = _uuid.uuid4().hex[:12]
            entry = {"id": vid, "name": file.filename or "saved data", "opened": now()}
            if zipfile.is_zipfile(tmp):
                with zipfile.ZipFile(tmp) as z:
                    names = z.namelist()
                    if "inventory_backup.db" in names:
                        dest = VIEW_DIR / f"{vid}.db"
                        with z.open("inventory_backup.db") as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out, 1024 * 1024)
                        entry.update(kind="archive", file=dest.name,
                                     photos=sum(1 for n in names if n.startswith("receipt_photos/")))
                    elif "sales_items.csv" in names:
                        day = read_day_zip(z, file.filename)
                        dest = VIEW_DIR / f"{vid}.json"
                        dest.write_text(_json.dumps(day), "utf-8")
                        entry.update(kind="day", file=dest.name, **{"from": day["date"], "to": day["date"]})
                    else:
                        raise HTTPException(422, "This ZIP is not a Tres Niños archive or day file")
            else:
                with open(tmp, "rb") as fh:
                    head = fh.read(16)
                if head != b"SQLite format 3\x00":
                    raise HTTPException(422, "Choose a TresNinos_archive ZIP, a TresNinos_<date> ZIP or a .db backup")
                dest = VIEW_DIR / f"{vid}.db"
                shutil.move(str(tmp), dest)
                entry.update(kind="database", file=dest.name)
            if entry["kind"] != "day":
                try:
                    c0 = sqlite3.connect(str(VIEW_DIR / entry["file"]))      # a copy of a live database is in WAL mode:
                    c0.execute("PRAGMA journal_mode=DELETE")                # make it one plain file (no -wal / -shm)
                    c0.close()
                except sqlite3.DatabaseError:
                    pass
                try:
                    lo, hi = db_range(VIEW_DIR / entry["file"])
                except HTTPException:
                    (VIEW_DIR / entry["file"]).unlink(missing_ok=True)
                    raise
                except sqlite3.DatabaseError:
                    (VIEW_DIR / entry["file"]).unlink(missing_ok=True)
                    raise HTTPException(422, "That file is damaged or not a database")
                entry.update(**{"from": lo, "to": hi})
            entry["bytes"] = (VIEW_DIR / entry["file"]).stat().st_size
            items = [x for x in view_index() if x["id"] != vid] + [entry]
            save_view_index(items)
        finally:
            tmp.unlink(missing_ok=True)
        note(conn, user, "Opened saved data", f"{entry['name']} ({entry.get('from') or '?'} to {entry.get('to') or '?'})")
        return entry

    @r.get("/archive/opened")
    def archive_opened(user=Depends(admin)):
        return list(reversed(view_index()))

    def get_view(vid):
        v = next((x for x in view_index() if x["id"] == vid), None)
        if not v:
            raise HTTPException(404, "That saved file is not open any more")
        return v

    @r.delete("/archive/opened/{vid}")
    def archive_close(vid: str, user=Depends(admin)):
        v = get_view(vid)
        for ext in ("", "-wal", "-shm", "-journal"):
            (VIEW_DIR / (v["file"] + ext)).unlink(missing_ok=True)
        save_view_index([x for x in view_index() if x["id"] != vid])
        return {"removed": vid}

    @r.get("/archive/opened/{vid}/days")
    def archive_days(vid: str, user=Depends(admin)):
        """every day that has something in the saved file: sales, staff present, order payments"""
        v = get_view(vid)
        if v["kind"] == "day":
            d = _json.loads((VIEW_DIR / v["file"]).read_text("utf-8"))
            return [{"date": d["date"], "sales": sum(p["total"] for p in d["salesByPerson"]),
                     "nSales": sum(p["count"] for p in d["salesByPerson"]), "present": len(d["present"]), "orders": 0,
                     "deliveries": len(d.get("deliveries") or [])}]
        c = ro_db(VIEW_DIR / v["file"])
        try:
            days = {}
            day = lambda x: days.setdefault(x, {"date": x, "sales": 0, "nSales": 0, "present": 0, "orders": 0})
            for x in c.execute("SELECT substr(created,1,10) d, SUM(total) t, COUNT(*) n FROM sales GROUP BY d"):
                if x["d"]:
                    day(x["d"]).update(sales=x["t"] or 0, nSales=x["n"])
            if has_table(c, "attendance"):
                for x in c.execute("SELECT date d, COUNT(*) n FROM attendance GROUP BY date"):
                    day(x["d"])["present"] = x["n"]
            if has_table(c, "bulk_orders"):
                for sql in ("SELECT substr(created,1,10) d, COUNT(*) n FROM bulk_orders GROUP BY d",
                            "SELECT date d, COUNT(*) n FROM bulk_order_payments GROUP BY date",
                            "SELECT completed_date d, COUNT(*) n FROM bulk_orders WHERE completed_date IS NOT NULL GROUP BY d"):
                    for x in c.execute(sql):
                        if x["d"]:
                            day(x["d"])["orders"] += x["n"]
            if has_table(c, "bulk_order_runs"):
                for x in c.execute("SELECT date d, COUNT(*) n FROM bulk_order_runs WHERE COALESCE(date,'')<>'' GROUP BY date"):
                    day(x["d"])["deliveries"] = x["n"]
            for x in days.values():
                x.setdefault("deliveries", 0)
            return sorted(days.values(), key=lambda x: x["date"])
        finally:
            c.close()

    def orders_of_day(c, d):
        if not has_table(c, "bulk_orders"):
            return []
        out = []
        ids = [x[0] for x in c.execute("""SELECT id FROM bulk_orders WHERE substr(created,1,10)=? OR completed_date=?
                                          UNION SELECT order_id FROM bulk_order_payments WHERE date=?
                                          UNION SELECT order_id FROM bulk_order_runs WHERE date=?""", (d, d, d, d))]
        for oid in ids:
            o = c.execute("SELECT * FROM bulk_orders WHERE id=?", (oid,)).fetchone()
            if not o:
                continue
            items = [{"name": x["name"], "qty": x["qty"], "unit": x["unit"] or "", "price": x["price"],
                      "delivered": bool(x["delivered"])} for x in c.execute("SELECT * FROM bulk_order_items WHERE order_id=?", (oid,))]
            pays = [{"date": x["date"], "amount": x["amount"], "note": x["note"] or ""}
                    for x in c.execute("SELECT * FROM bulk_order_payments WHERE order_id=? ORDER BY date, id", (oid,))]
            total = sum(i["qty"] * i["price"] for i in items)
            if "charge_delivery" in o.keys() and o["charge_delivery"] and has_table(c, "transports"):
                total += sum(run_cost(c, x) for x in c.execute("SELECT * FROM bulk_order_runs WHERE order_id=?", (oid,)))  # + delivery
            events = []
            if (o["created"] or "")[:10] == d:
                events.append("order made")
            if any(p["date"] == d for p in pays):
                events.append(f"paid {peso_txt(sum(p['amount'] for p in pays if p['date'] == d))}")
            if c.execute("SELECT 1 FROM bulk_order_runs WHERE order_id=? AND date=?", (oid, d)).fetchone():
                events.append("delivery")
            if o["completed_date"] == d:
                events.append("completed")
            paid = sum(p["amount"] for p in pays if p["date"] <= d)
            out.append({"id": oid, "customer": o["customer"], "items": items, "total": round(total, 2),
                        "paidToday": round(sum(p["amount"] for p in pays if p["date"] == d), 2), "paidSoFar": round(paid, 2),
                        "status": "done" if o["completed_date"] and o["completed_date"] <= d else "active", "events": events,
                        "payments": [p for p in pays if p["date"] <= d]})
        return out

    def deliveries_of_day(c, d):
        """the truck runs of day d in a saved database (older files may not have crews / addresses yet)"""
        if not (has_table(c, "bulk_order_runs") and has_table(c, "bulk_orders")):
            return []
        rcols = {x[1] for x in c.execute("PRAGMA table_info(bulk_order_runs)")}
        ocols = {x[1] for x in c.execute("PRAGMA table_info(bulk_orders)")}
        crew = has_table(c, "run_crew") and has_table(c, "employees")
        trucks = has_table(c, "transports")
        out = []
        for x in c.execute(f"""SELECT r.*, o.customer{', o.address' if 'address' in ocols else ''}
                               {', t.truck_name, t.plate' if trucks else ''} FROM bulk_order_runs r LEFT JOIN bulk_orders o ON o.id=r.order_id
                               {'LEFT JOIN transports t ON t.id=r.transport_id' if trucks else ''}
                               WHERE r.date=? AND (o.id IS NOT NULL OR r.order_id IS NULL)
                               ORDER BY COALESCE(o.customer, '')""", (d,)).fetchall():
            driver = ""
            if "driver_id" in rcols and x["driver_id"] and has_table(c, "employees"):
                driver = (c.execute("SELECT name FROM employees WHERE id=?", (x["driver_id"],)).fetchone() or [""])[0]
            helpers = [h[0] for h in c.execute("""SELECT e.name FROM run_crew rc JOIN employees e ON e.id=rc.employee_id
                                                   WHERE rc.run_id=? ORDER BY e.name""", (x["id"],))] if crew else []
            mode = (x["mode"] if "mode" in rcols else None) or "truck"          # saved files from before r15: our truck
            who = {"pickup": "Customer picks up", "other": (x["carrier"] if "carrier" in rcols else None) or "Someone else"}.get(mode)
            addr = (x["address"] if "address" in ocols else "") or ""
            if x["order_id"] is None:                  # a truck trip of its own (round 18)
                xd = dict(x)
                x, addr = {**xd, "customer": trip_label(xd)}, xd.get("place") or ""
            out.append({"customer": x["customer"], "address": addr,
                        "truck": who or (x["truck_name"] if trucks else "") or "", "plate": "" if who else (x["plate"] if trucks else "") or "",
                        "trips": x["trips"], "driver": driver or "", "helpers": helpers, "mode": mode,
                        "done": bool(x["done"]) if "done" in rcols else False})
        return out

    @r.get("/archive/opened/{vid}/day/{day}")
    def archive_day(vid: str, day: str, user=Depends(admin)):
        """one day of the saved file: staff present (with overtime), each sales person with the items they sold, orders"""
        check_day(day)
        v = get_view(vid)
        if v["kind"] == "day":
            d = _json.loads((VIEW_DIR / v["file"]).read_text("utf-8"))
            if d["date"] != day:
                raise HTTPException(404, "This day file only has " + d["date"])
            return {**d, "kind": "day"}
        c = ro_db(VIEW_DIR / v["file"])
        try:
            present = []
            if has_table(c, "attendance"):
                wh = has_table(c, "work_hours")
                for x in c.execute(f"""SELECT e.name, e.position, a.time_in{', w.ot_hours, w.ut_hours' if wh else ''}
                                       FROM attendance a JOIN employees e ON e.id=a.employee_id
                                       {'LEFT JOIN work_hours w ON w.employee_id=a.employee_id AND w.date=a.date' if wh else ''}
                                       WHERE a.date=? ORDER BY a.time_in""", (day,)):
                    present.append({"name": x["name"], "position": x["position"] or "", "timeIn": x["time_in"],
                                    "ot": (x["ot_hours"] if wh else 0) or 0, "ut": (x["ut_hours"] if wh else 0) or 0})
            by = {}
            has_users = has_table(c, "users")
            sql = ("""SELECT s.*, COALESCE(u.display_name, u.username, 'Desktop') person FROM sales s
                      LEFT JOIN users u ON u.id=s.user_id WHERE substr(s.created,1,10)=? ORDER BY s.id""" if has_users else
                   "SELECT s.*, 'Desktop' person FROM sales s WHERE substr(s.created,1,10)=? ORDER BY s.id")
            for sa in c.execute(sql, (day,)).fetchall():
                p_ = by.setdefault(sa["person"], {"person": sa["person"], "count": 0, "items": {}, "total": 0.0})
                p_["count"] += 1
                for it in c.execute("""SELECT si.qty, si.sell_price, i.name, i.unit FROM sale_items si LEFT JOIN items i ON i.id=si.item_id
                                       WHERE si.sale_id=?""", (sa["id"],)):
                    g = p_["items"].setdefault((it["name"], it["unit"]), {"item": it["name"] or "(deleted item)", "unit": it["unit"] or "",
                                                                         "qty": 0, "amount": 0})
                    g["qty"] += it["qty"] or 0
                    g["amount"] += (it["qty"] or 0) * (it["sell_price"] or 0)
                p_["total"] += sa["total"] or 0
            people = [{"person": p_["person"], "count": p_["count"], "total": round(p_["total"], 2),
                       "items": sorted(p_["items"].values(), key=lambda i: -i["amount"])} for p_ in by.values()]
            return {"kind": v["kind"], "date": day, "present": present, "salesByPerson": sorted(people, key=lambda p: -p["total"]),
                    "orders": orders_of_day(c, day), "deliveries": deliveries_of_day(c, day)}
        finally:
            c.close()

    # ================= TRANSFER (menu: Transfer) =================
    # Move the SETUP of this shop to another laptop running the same system: employees (with photos and their day
    # records), loans & bills, maintenance, benefits, supply (items, prices, levels, photos), trucks, orders, bank
    # accounts, cash entries. Export = one ZIP (setup.json + photos). Import = preview what is new / changed / the same,
    # tick what to apply, Apply. Rows are matched by name, nothing is ever deleted, applying twice adds nothing twice.
    # NOT moved: sales and receipts (Calendar -> archive), accounts / passwords / the system password.
    TRANSFER_DIR = BASE / "transfer_in"
    SECTIONS = {
        "employees": "Employees (with photos, attendance, overtime, cash advances, payroll)",
        "obligations": "Loans & bills (with their payments)",
        "maintenance": "Maintenance (with payments)",
        "benefits": "Benefits / contributions (SSS, PhilHealth…)",
        "supply": "Supply: items, prices, A-B-C levels, units, photos",
        "trucks": "Trucks",
        "orders": "Orders (Sales → Add Order) with items, payments, deliveries",
        "banks": "Payments: cash and bank payment lines (old bank accounts too)",
        "cash": "Cash on hand entries (added / taken out by hand)",
        "sales": "Sales: every sale with its items, the sales person and cash / bank (no stock change)",
        "drafts": "Delivery orders read from the phones, waiting for the owner (with the receipt photo)",
    }

    def dicts(conn, sql, *a):
        return [dict(x) for x in conn.execute(sql, a).fetchall()]

    def strip_(d, *drop):
        return {k: v for k, v in d.items() if k not in ("id",) + drop}

    def k_(*v):
        return "|".join(str(x or "").strip().lower() for x in v)

    def build_setup(conn, files):
        """files: {zip path: disk path} filled with the photos to pack"""
        sec = {}
        emps = []
        for e in dicts(conn, "SELECT * FROM employees ORDER BY id"):
            row = strip_(e, "user_id", "photo", "created")
            if e["user_id"]:
                u = conn.execute("SELECT username, display_name FROM users WHERE id=?", (e["user_id"],)).fetchone()
                if u:
                    row["login"] = {"username": u["username"], "name": u["display_name"] or u["username"]}
            if e["photo"] and (AVATAR_DIR / e["photo"]).exists():
                row["photo"] = f"photos/employees/{e['id']}.jpg"
                files[row["photo"]] = AVATAR_DIR / e["photo"]
            row["attendance"] = [strip_(x, "employee_id") for x in dicts(conn, "SELECT date, time_in FROM attendance WHERE employee_id=?", e["id"])]
            row["hours"] = dicts(conn, "SELECT date, ot_hours, ut_hours, note FROM work_hours WHERE employee_id=?", e["id"])
            row["advances"] = dicts(conn, "SELECT date, amount, note FROM employee_cash_advances WHERE employee_id=?", e["id"])
            row["payments"] = dicts(conn, "SELECT kind, date, amount, note FROM employee_payments WHERE employee_id=?", e["id"])
            emps.append(row)
        sec["employees"] = {"positions": [x["name"] for x in conn.execute("SELECT name FROM positions ORDER BY id")], "rows": emps}
        sec["obligations"] = [{**strip_(o, "created"), "payments": dicts(conn, "SELECT date, amount, note FROM obligation_payments WHERE obligation_id=?", o["id"])}
                              for o in dicts(conn, "SELECT * FROM obligations ORDER BY id")]
        sec["maintenance"] = [{**strip_(m), "payments": dicts(conn, "SELECT date, amount FROM maintenance_payments WHERE item_id=?", m["id"])}
                              for m in dicts(conn, "SELECT * FROM maintenance_items ORDER BY id")]
        sec["benefits"] = [{"label": t["label"], "items": [strip_(c, "custom_type_id") for c in dicts(conn, "SELECT * FROM custom_items WHERE custom_type_id=?", t["id"])]}
                           for t in dicts(conn, "SELECT * FROM custom_types ORDER BY id")]
        prods = []
        for p_ in dicts(conn, "SELECT * FROM products ORDER BY id"):
            row = {"name": p_["name"], "icon_key": p_["icon_key"]}
            if p_.get("image") and (PRODUCT_DIR / p_["image"]).exists():
                row["photo"] = f"photos/products/{p_['id']}.jpg"
                files[row["photo"]] = PRODUCT_DIR / p_["image"]
            row["items"] = [{**strip_(i, "product_id"), "sourcing": dicts(conn, """SELECT source, unit_cost, transfer_cost, maintenance_cost
                                                                                  FROM supply_sourcing WHERE item_id=?""", i["id"])}
                            for i in dicts(conn, "SELECT * FROM items WHERE product_id=? ORDER BY id", p_["id"])]
            prods.append(row)
        loose = [strip_(i, "product_id") for i in dicts(conn, "SELECT * FROM items WHERE product_id IS NULL ORDER BY id")]
        if loose:
            prods.append({"name": None, "items": loose})
        sec["supply"] = {"units": dicts(conn, "SELECT name, to_kg FROM units ORDER BY id"), "products": prods}
        sec["trucks"] = [strip_(t, "created") for t in dicts(conn, "SELECT * FROM transports ORDER BY id")]
        trucks = {t["id"]: t for t in dicts(conn, "SELECT * FROM transports")}
        orders = []
        for o in dicts(conn, "SELECT * FROM bulk_orders ORDER BY id"):
            items = [strip_(i, "order_id") for i in dicts(conn, "SELECT * FROM bulk_order_items WHERE order_id=?", o["id"])]
            for i in items:
                it = conn.execute("SELECT name FROM items WHERE id=?", (i.pop("item_id", None),)).fetchone()
                i["item"] = it[0] if it else None
            runs = [{"truck": (trucks.get(x["transport_id"]) or {}).get("truck_name"), "plate": (trucks.get(x["transport_id"]) or {}).get("plate"),
                     "trips": x["trips"], "date": x["date"], "done": x.get("done") or 0,
                     "mode": x.get("mode") or "truck", "carrier": x.get("carrier"), "carrier_cost": x.get("carrier_cost"),
                     "driver": (conn.execute("SELECT name FROM employees WHERE id=?", (x["driver_id"],)).fetchone() or [None])[0]
                     if x.get("driver_id") else None,
                     "helpers": [h[0] for h in conn.execute("""SELECT e.name FROM run_crew c JOIN employees e ON e.id=c.employee_id
                                                               WHERE c.run_id=?""", (x["id"],))]}
                    for x in dicts(conn, "SELECT * FROM bulk_order_runs WHERE order_id=?", o["id"])]
            orders.append({**strip_(o), "items": items, "runs": runs,
                           "payments": dicts(conn, "SELECT id, date, amount, note FROM bulk_order_payments WHERE order_id=?", o["id"])})
        sec["orders"] = orders
        okey = {o["id"]: (o["customer"], o["created"]) for o in dicts(conn, "SELECT id, customer, created FROM bulk_orders")}
        sb = sales_bank(conn)
        sec["banks"] = [{**strip_(a, "created"), "sales_bank": bool(sb and sb["id"] == a["id"]), "entries": [
            {**strip_(e, "account_id", "order_id", "order_payment_id", "photo", "created"),
             "order": list(okey[e["order_id"]]) if e["order_id"] in okey else None}
            for e in dicts(conn, "SELECT * FROM pay_entries WHERE account_id=? ORDER BY id", a["id"])]}
            for a in dicts(conn, "SELECT * FROM pay_accounts ORDER BY id")]
        sec["banks"].append({"bank": None, "entries": [
            {**strip_(e, "account_id", "order_id", "order_payment_id", "photo", "created"),
             "order": list(okey[e["order_id"]]) if e["order_id"] in okey else None}
            for e in dicts(conn, "SELECT * FROM pay_entries WHERE account_id IS NULL ORDER BY id")]})
        sec["cash"] = [strip_(x) for x in dicts(conn, "SELECT * FROM cash_entries ORDER BY id")]
        for o in sec["orders"]:                      # payment ids only served to link bank lines; not needed in the file
            for p_ in o["payments"]:
                p_.pop("id", None)
        sec["drafts"] = build_drafts(conn, files)
        return sec

    def build_drafts(conn, files):
        """delivery orders still waiting for the owner, each with its receipt (photo + what the reader saw)"""
        out = []
        for x in dicts(conn, "SELECT * FROM delivery_drafts WHERE status='new' ORDER BY id"):
            u = conn.execute("SELECT username, display_name FROM users WHERE id=?", (x["user_id"],)).fetchone() if x["user_id"] else None
            try:
                data = _json_mod.loads(x["data"] or "{}")
            except ValueError:
                data = {}
            row = {"created": x["created"], "customer": x["customer"], "address": x["address"], "delivery_date": x["delivery_date"],
                   "data": data, "person": {"username": u["username"], "name": u["display_name"] or u["username"]} if u else None}
            rc = conn.execute("SELECT * FROM receipts WHERE id=?", (x["receipt_id"],)).fetchone() if x["receipt_id"] else None
            if rc:
                f = UPLOAD_DIR / f"{rc['id']}.jpg"
                if not f.exists() and rc["image"]:
                    f = Path(rc["image"])
                if f.exists() and f.is_file():
                    row["photo"] = f"photos/receipts/draft{x['id']}.jpg"
                    files[row["photo"]] = f
                row["receipt"] = {
                    **{k: rc[k] for k in ("created", "finished", "seconds", "device", "model", "pay_mode", "pay_note", "pay_sure")},
                    "lines": [{"ocr_text": l["ocr_text"], "item": l["name"], "score": l["score"], "qty": l["qty"], "verified": l["verified"]}
                              for l in conn.execute("""SELECT l.*, i.name FROM receipt_lines l JOIN items i ON i.id=l.item_id
                                                       WHERE l.receipt_id=? AND l.sold=0 ORDER BY l.id""", (rc["id"],))],
                    "unmatched": [{"ocr_text": m["ocr_text"], "item_text": m["item_text"], "qty": m["qty"]}
                                  for m in conn.execute("SELECT * FROM receipt_unmatched WHERE receipt_id=? ORDER BY id", (rc["id"],))]}
            out.append(row)
        return out

    def build_sales(conn, start=None, end=None):
        out = []
        for sa in conn.execute("""SELECT s.*, u.username, COALESCE(u.display_name, u.username) uname FROM sales s
                                  LEFT JOIN users u ON u.id=s.user_id
                                  WHERE (? IS NULL OR substr(s.created,1,10) >= ?) AND (? IS NULL OR substr(s.created,1,10) <= ?)
                                  ORDER BY s.id""", (start, start, end, end)).fetchall():
            out.append({"created": sa["created"], "total": sa["total"], "profit": sa["profit"],
                        "pay_mode": sa["pay_mode"], "pay_note": sa["pay_note"],
                        "person": {"username": sa["username"], "name": sa["uname"]} if sa["username"] else None,
                        "items": [{"item": it["name"], "unit": it["unit"], "qty": it["qty"], "sell_price": it["sell_price"],
                                   "cost_price": it["cost_price"]}
                                  for it in conn.execute("""SELECT si.*, i.name, i.unit FROM sale_items si LEFT JOIN items i ON i.id=si.item_id
                                                            WHERE si.sale_id=? ORDER BY si.id""", (sa["id"],)) if it["name"]]})
        return out

    @r.get("/transfer/export")
    def transfer_export(sales: bool = False, salesFrom: Optional[str] = None, salesTo: Optional[str] = None,
                        user=Depends(admin_q), conn=Depends(get_db)):
        """ZIP: setup.json + photos/. Open it on the other laptop: Transfer -> Read a setup file.
        sales=1 also puts every sale in (with its items and the sales person), optionally only salesFrom..salesTo."""
        import socket
        files = {}
        sec = build_setup(conn, files)
        if sales:
            sec["sales"] = build_sales(conn, check_day(salesFrom) if salesFrom else None, check_day(salesTo) if salesTo else None)
        counts = {"employees": len(sec["employees"]["rows"]), "obligations": len(sec["obligations"]),
                  "maintenance": len(sec["maintenance"]), "benefits": sum(len(b["items"]) for b in sec["benefits"]),
                  "supply": sum(len(p_["items"]) for p_ in sec["supply"]["products"]), "trucks": len(sec["trucks"]),
                  "orders": len(sec["orders"]), "banks": sum(len(b["entries"]) for b in sec["banks"]), "cash": len(sec["cash"]),
                  "sales": len(sec.get("sales") or []), "drafts": len(sec.get("drafts") or [])}
        doc = {"app": "tres-ninos-shop", "kind": "setup", "version": 1, "made": now(), "from": socket.gethostname(),
               "by": user["display_name"] or user["username"], "counts": counts, "sections": sec}
        path = temp_zip()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("setup.json", _json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"))
            for arc, f in files.items():
                z.write(f, arc)
            z.writestr("README.txt", ("Tres Niños setup file. On the other laptop: Transfer -> Read a setup file -> tick what to apply.\n"
                                      + ("Has the sales too (no receipt photos). " if sales else "Has no sales. ")
                                      + "Has no receipt photos, accounts or passwords.\n").encode("utf-8"))
        note(conn, user, "Transfer", "setup file made (" + ", ".join(f"{v} {k}" for k, v in counts.items() if v) + ")")
        return send_zip(path, f"TresNinos_setup_{datetime.now():%Y-%m-%d_%H%M}.zip")

    # ---- an inventory LIST (CSV from Excel) instead of a setup ZIP: Transfer -> Read a setup file accepts both ----
    INV_COLS = ["Product", "Type", "Unit", "Selling price", "Stock", "Standard cost", "A", "B", "C"]
    INV_ALIASES = {"product": "Product", "item": "Product", "name": "Product", "type": "Type", "spec": "Type", "variant": "Type",
                   "unit": "Unit", "selling price": "Selling price", "price": "Selling price", "sell price": "Selling price",
                   "stock": "Stock", "quantity": "Stock", "qty": "Stock", "standard cost": "Standard cost", "cost": "Standard cost",
                   "cost price": "Standard cost", "a": "A", "b": "B", "c": "C", "level a": "A", "level b": "B", "level c": "C"}

    def inventory_csv_doc(raw):
        """A CSV list (columns: Product, Type, Unit, Selling price, Stock, Standard cost, A, B, C - one row per type)
        -> the same setup document a setup ZIP holds (only the Supply section). Raises HTTPException(422) with the row."""
        for enc in ("utf-8-sig", "cp1252"):                    # Excel: "CSV UTF-8" or plain "CSV (Comma delimited)"
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        first = text.split("\n", 1)[0]
        delim = ";" if first.count(";") > first.count(",") else ("\t" if first.count("\t") > first.count(",") else ",")
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        head = next((k for k, r_ in enumerate(rows) if any(INV_ALIASES.get(c.strip().lower()) == "Product" for c in r_)), None)
        if head is None:
            raise HTTPException(422, "This CSV has no 'Product' column. Use the inventory list from Transfer -> Inventory list (Excel).")
        cols = [INV_ALIASES.get(c.strip().lower()) for c in rows[head]]

        def num(v, line, what, required=False):
            v = (v or "").strip()
            if _re_mod.search(r"\d", v):                     # "₱1,200.50", "P 75", "?75" (Excel CSV without UTF-8) -> 1200.5
                v = _re_mod.sub(r"[^\d.\-]", "", v.replace(",", ""))
            if v in ("", "-", "—"):
                if required:
                    raise HTTPException(422, f"Row {line}: {what} is empty.")
                return None
            try:
                return float(v)
            except ValueError:
                raise HTTPException(422, f"Row {line}: {what} '{v}' is not a number.")
        prods, units, seen = {}, set(), set()
        for k, r_ in enumerate(rows[head + 1:], start=head + 2):
            d = {c: (r_[j].strip() if j < len(r_) else "") for j, c in enumerate(cols) if c}
            if not d.get("Product"):
                if any(d.values()):
                    raise HTTPException(422, f"Row {k}: the Product is empty.")
                continue
            product, spec = " ".join(d["Product"].split()), " ".join(d.get("Type", "").split())
            name = item_name(product, spec)
            if name.lower() in seen:
                raise HTTPException(422, f"Row {k}: '{name}' is in the list twice. Give one of them another Type, e.g. '(other)'.")
            seen.add(name.lower())
            stock = num(d.get("Stock"), k, "Stock")
            cost = num(d.get("Standard cost"), k, "Standard cost")
            unit = d.get("Unit") or "pcs"
            units.add(unit)
            sell = num(d.get("Selling price"), k, "Selling price") or 0
            prods.setdefault(product.lower(), {"name": product, "items": []})["items"].append({
                "name": name, "spec": spec or None, "unit": unit, "sell_price": sell,
                "cost_price": cost if cost else shop_db.assumed_cost(sell), "cost_auto": 0 if cost else 1, "quantity": stock or 0,
                "qty_unknown": 1 if stock is None else 0, "level_a": num(d.get("A"), k, "A"), "level_b": num(d.get("B"), k, "B"),
                "level_c": num(d.get("C"), k, "C"), "sourcing": []})
        if not prods:
            raise HTTPException(422, "The inventory list has no items.")
        n = sum(len(p_["items"]) for p_ in prods.values())
        return {"app": "tres-ninos-shop", "kind": "setup", "version": 1, "made": now(), "from": "inventory list (CSV)",
                "by": "", "counts": {"supply": n},
                "sections": {"supply": {"units": [{"name": u, "to_kg": 1 if u.lower() == "kg" else None} for u in sorted(units)],
                                        "products": list(prods.values())}}}

    @r.get("/transfer/inventory-list")
    def inventory_list(user=Depends(admin_q), conn=Depends(get_db)):
        """Supply as an inventory list (CSV for Excel): edit it or add rows, save it as CSV, then Read a setup file.
        No items yet: two example rows."""
        def n_(v):
            return "" if v is None else f"{v:g}"
        rows = []
        for i in conn.execute("""SELECT i.*, p.name pname FROM items i LEFT JOIN products p ON p.id=i.product_id
                                 WHERE i.deleted_at IS NULL ORDER BY COALESCE(p.name, i.name), i.id"""):
            rows.append([i["pname"] or i["name"], (i["spec"] or "") if i["pname"] else "", i["unit"], n_(i["sell_price"]),
                         "" if i["qty_unknown"] else n_(i["quantity"]), "" if i["cost_auto"] else n_(i["cost_price"]),
                         n_(i["level_a"]), n_(i["level_b"]), n_(i["level_c"])])
        if not rows:
            rows = [["Boysen", "QDE Maroon (Gallon)", "gallons", "910", "4", "", "2", "", ""],
                    ["Common Wire Nail", "#2", "kg", "75", "250", "", "", "", ""]]
        f = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        f.write(csv_bytes(INV_COLS, rows))
        f.close()
        return FileResponse(f.name, media_type="text/csv", filename=f"TresNinos_inventory_list_{datetime.now():%Y-%m-%d}.csv",
                            background=BackgroundTask(lambda: __import__("os").remove(f.name)))

    # ---- reading a setup file ----
    def load_setup(token):
        if not _re.fullmatch(r"[0-9a-f]{32}", token or ""):
            raise HTTPException(404, "Read the setup file again")
        f = TRANSFER_DIR / f"{token}.zip"
        if not f.exists():
            raise HTTPException(404, "Read the setup file again")
        with zipfile.ZipFile(f) as z:
            doc = _json.loads(z.read("setup.json").decode("utf-8"))
        return f, doc

    def same_val(a, b):
        """equal for the plan: 198 and 198.0 are the same, None and "" are the same"""
        a = "" if a is None else a
        b = "" if b is None else b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
            return abs(float(a) - float(b)) < 1e-9
        return str(a) == str(b)

    def diff(cur, new, fields):
        return [k for k in fields if k in new and not same_val(cur.get(k), new.get(k))]

    EMP_F = ["position", "rate_per_hour", "per_day_rate", "present_days", "absent_days", "overtime_hours", "undertime_hours",
             "notes", "active", "temporary"]
    OBL_F = ["party", "start_date", "end_date", "freq_value", "freq_unit", "principal", "interest_rate", "final_payment", "status"]
    MNT_F = ["amount", "freq_value", "freq_unit", "start_date", "last_paid", "notes"]
    CUS_F = ["amount", "basis", "notes"]
    ITEM_F = ["unit", "spec", "sell_price", "cost_price", "level_a", "level_b", "level_c", "cost_auto", "qty_unknown", "deleted_at"]
    TRK_F = ["capacity", "capacity_value", "capacity_unit", "cost"]
    ORD_F = ["start_date", "end_date", "status", "completed_date", "address", "charge_delivery"]
    BANK_F = ["holder", "opening", "opening_date"]

    def line_key(e):
        """a cash / bank payment line (no account) in a setup file: day, amount, in/out, who, which order"""
        o = e.get("order") or [None, None]
        return k_(e["date"], e["amount"], e["direction"], e.get("party"), o[0], o[1])

    def line_keys(conn):
        """the same keys for the lines already here, counted"""
        from collections import Counter
        return Counter(k_(x["date"], x["amount"], x["direction"], x["party"], x["customer"], x["created"]) for x in conn.execute(
            """SELECT e.date, e.amount, e.direction, e.party, o.customer, o.created FROM pay_entries e
               LEFT JOIN bulk_orders o ON o.id=e.order_id WHERE e.account_id IS NULL"""))

    def sales_here(conn):
        """(time, total, sales person) -> [(sale id, cash/bank)] of the sales here: equal sales are counted, not merged"""
        out = {}
        for x in conn.execute("""SELECT s.id, s.created, s.total, s.pay_mode, u.username FROM sales s
                                 LEFT JOIN users u ON u.id=s.user_id ORDER BY s.id"""):
            out.setdefault(k_(x["created"], round(x["total"] or 0, 2), x["username"]), []).append((x["id"], x["pay_mode"] or "cash"))
        return out

    def pay_changed(sa, mode_here):
        return sa.get("pay_mode") in ("cash", "bank") and sa["pay_mode"] != mode_here

    def draft_here(conn, x):
        """the same delivery order is already here: same sales person + same moment it was read"""
        return conn.execute("""SELECT d.id FROM delivery_drafts d LEFT JOIN users u ON u.id=d.user_id
                               WHERE d.created=? AND COALESCE(u.username,'')=COALESCE(?,'') COLLATE NOCASE""",
                            (x.get("created"), (x.get("person") or {}).get("username"))).fetchone()

    def file_run_key(x):
        """a delivery run of a setup file: day + who delivers (our truck: which one; else pick-up / someone else + who)"""
        mode = x.get("mode") if x.get("mode") in RUN_MODES else "truck"
        if mode == "truck":
            return k_(x.get("date"), x.get("truck"), x.get("plate"))
        return k_(x.get("date"), mode, x.get("carrier"))

    def runs_here(conn, oid):
        """the delivery runs of an order here, by day + who delivers (as file_run_key): {key: [run rows]}"""
        out = {}
        for x in conn.execute("""SELECT r.*, t.truck_name, t.plate FROM bulk_order_runs r LEFT JOIN transports t ON t.id=r.transport_id
                                 WHERE r.order_id=? ORDER BY r.id""", (oid,)).fetchall():
            mode = run_mode(x)
            key = k_(x["date"], x["truck_name"], x["plate"]) if mode == "truck" else k_(x["date"], mode, x["carrier"])
            out.setdefault(key, []).append(x)
        return out

    def crew_names(conn, run):
        d_ = (conn.execute("SELECT name FROM employees WHERE id=?", (run["driver_id"],)).fetchone() or [None])[0] if run["driver_id"] else None
        hs = sorted(h[0].lower() for h in conn.execute("""SELECT e.name FROM run_crew c JOIN employees e ON e.id=c.employee_id
                                                          WHERE c.run_id=?""", (run["id"],)))
        return (d_ or "").lower(), hs

    def runs_diff(conn, oid, runs):
        """(runs in the file that this order does not have, runs whose crew or delivered tick differ)"""
        here = {k: list(v) for k, v in runs_here(conn, oid).items()}
        n_new = n_crew = 0
        for x in runs:
            key = file_run_key(x)
            if not here.get(key):
                n_new += 1
                continue
            run = here[key].pop(0)
            drv, hs = crew_names(conn, run)
            if (drv != (x.get("driver") or "").lower() or hs != sorted(h.lower() for h in x.get("helpers") or [])
                    or bool(run["done"]) != bool(x.get("done"))):
                n_crew += 1
        return n_new, n_crew

    def plan(conn, doc):
        """what applying would do, per section: rows that are new, changed (which fields) or the same"""
        sec = doc.get("sections") or {}
        out = {}

        def add(name, label, state, fields=None):
            b = out.setdefault(name, {"key": name, "title": SECTIONS[name], "new": [], "changed": [], "same": 0})
            if state == "same":
                b["same"] += 1
            else:
                b[state].append({"label": label, "fields": fields or []})

        for e in (sec.get("employees") or {}).get("rows", []):
            cur = conn.execute("SELECT * FROM employees WHERE name=? COLLATE NOCASE", (e["name"],)).fetchone()
            ch = diff(dict(cur), e, EMP_F) if cur else None
            lg = e.get("login") or {}
            label = e["name"] + (f" · phone login @{lg['username']}" + (" (password set)" if lg.get("password_hash") else "")
                                 if lg.get("username") else "")
            add("employees", label, "new" if not cur else "changed" if ch else "same", ch)
        for o in sec.get("obligations") or []:
            cur = conn.execute("SELECT * FROM obligations WHERE name=? COLLATE NOCASE AND kind=?", (o["name"], o["kind"])).fetchone()
            ch = diff(dict(cur), o, OBL_F) if cur else None
            add("obligations", f"{o['name']} ({o['kind']})", "new" if not cur else "changed" if ch else "same", ch)
        for m in sec.get("maintenance") or []:
            cur = conn.execute("SELECT * FROM maintenance_items WHERE name=? COLLATE NOCASE", (m["name"],)).fetchone()
            ch = diff(dict(cur), m, MNT_F) if cur else None
            add("maintenance", m["name"], "new" if not cur else "changed" if ch else "same", ch)
        for b in sec.get("benefits") or []:
            t = conn.execute("SELECT id FROM custom_types WHERE label=? COLLATE NOCASE", (b["label"],)).fetchone()
            for c in b["items"]:
                cur = conn.execute("SELECT * FROM custom_items WHERE custom_type_id=? AND name=? COLLATE NOCASE",
                                   (t[0], c["name"])).fetchone() if t else None
                ch = diff(dict(cur), c, CUS_F) if cur else None
                add("benefits", f"{b['label']}: {c['name']}", "new" if not cur else "changed" if ch else "same", ch)
        for p_ in (sec.get("supply") or {}).get("products", []):
            for i in p_["items"]:
                cur = conn.execute("SELECT * FROM items WHERE name=? COLLATE NOCASE", (i["name"],)).fetchone()
                ch = diff(dict(cur), i, ITEM_F + ["quantity"]) if cur else None
                add("supply", i["name"], "new" if not cur else "changed" if ch else "same", ch)
        for t in sec.get("trucks") or []:
            cur = conn.execute("SELECT * FROM transports WHERE truck_name=? COLLATE NOCASE AND COALESCE(plate,'')=COALESCE(?,'')",
                               (t["truck_name"], t.get("plate"))).fetchone()
            ch = diff(dict(cur), t, TRK_F) if cur else None
            add("trucks", f"{t['truck_name']} {t.get('plate') or ''}".strip(), "new" if not cur else "changed" if ch else "same", ch)
        for o in sec.get("orders") or []:
            cur = conn.execute("SELECT * FROM bulk_orders WHERE customer=? COLLATE NOCASE AND COALESCE(created,'')=COALESCE(?,'')",
                               (o["customer"], o.get("created"))).fetchone()
            ch = diff(dict(cur), o, ORD_F) if cur else None
            if cur:
                from collections import Counter
                have = Counter(k_(x["date"], x["amount"]) for x in conn.execute("SELECT date, amount FROM bulk_order_payments WHERE order_id=?", (cur["id"],)))
                if Counter(k_(p_["date"], p_["amount"]) for p_ in o["payments"]) - have:
                    ch = (ch or []) + ["payments"]
                n_new, n_crew = runs_diff(conn, cur["id"], o.get("runs") or [])
                if n_new:
                    ch = (ch or []) + [f"{n_new} delivery run(s)"]
                if n_crew:
                    ch = (ch or []) + [f"crew / delivered of {n_crew} run(s)"]
            add("orders", f"{o['customer']} ({(o.get('created') or '')[:10]})", "new" if not cur else "changed" if ch else "same", ch)
        for a in sec.get("banks") or []:
            if a.get("bank"):
                cur = conn.execute("""SELECT * FROM pay_accounts WHERE bank=? COLLATE NOCASE AND COALESCE(nickname,'')=COALESCE(?,'')
                                      AND COALESCE(last4,'')=COALESCE(?,'')""", (a["bank"], a.get("nickname"), a.get("last4"))).fetchone()
                n_new = len(a["entries"])
                if cur:
                    have = {k_(x["date"], x["amount"], x["direction"], x["ref"]) for x in conn.execute("SELECT * FROM pay_entries WHERE account_id=?", (cur["id"],))}
                    n_new = sum(1 for e in a["entries"] if k_(e["date"], e["amount"], e["direction"], e.get("ref")) not in have)
                ch = (diff(dict(cur), a, BANK_F) + ([f"{n_new} passbook line(s)"] if n_new else [])) if cur else None
                add("banks", f"{a['bank']} {a.get('nickname') or ''}".strip(), "new" if not cur else "changed" if ch else "same", ch)
            elif a["entries"]:
                have = line_keys(conn)
                n_new = 0
                for e in a["entries"]:
                    if have[line_key(e)] > 0:
                        have[line_key(e)] -= 1
                    else:
                        n_new += 1
                add("banks", "Cash / bank payment lines", "changed" if n_new else "same", [f"{n_new} payment line(s)"] if n_new else None)
        have_cash = {k_(x["date"], x["direction"], x["amount"], x["note"]) for x in conn.execute("SELECT * FROM cash_entries")}
        for c in sec.get("cash") or []:
            add("cash", f"{c['date']} {c['direction']} {peso_txt(c['amount'])}",
                "same" if k_(c["date"], c["direction"], c["amount"], c.get("note")) in have_cash else "new")
        if sec.get("sales"):
            have = sales_here(conn)
            per = {}
            for sa in sec["sales"]:
                who = (sa.get("person") or {}).get("name") or "Desktop"
                key = k_(sa["created"], round(sa["total"] or 0, 2), (sa.get("person") or {}).get("username"))
                mine = have.get(key)
                st, why = "new", None
                if mine:
                    _sid, mode = mine.pop(0)
                    st, why = ("changed", [f"paid by {sa['pay_mode']}"]) if pay_changed(sa, mode) else ("same", None)
                add("sales", f"{sa['created'][:16].replace('T', ' ')} · {who} · {peso_txt(sa['total'])}", st, why)
                p_ = per.setdefault(who, [0, 0.0, None, None])
                p_[0] += 1
                p_[1] += sa["total"] or 0
                d_ = sa["created"][:10]
                p_[2] = min(p_[2] or d_, d_)
                p_[3] = max(p_[3] or d_, d_)
            out["sales"]["summary"] = [f"{k}: {v[0]} sale(s), {peso_txt(v[1])} ({v[2]} to {v[3]})"
                                       for k, v in sorted(per.items(), key=lambda kv: -kv[1][1])]
        for x in sec.get("drafts") or []:
            who = (x.get("person") or {}).get("name") or "phone"
            add("drafts", f"{who} · {x.get('customer') or 'no customer name'} · {(x.get('created') or '')[:16].replace('T', ' ')}"
                          f"{' · with photo' if x.get('photo') else ''}", "same" if draft_here(conn, x) else "new")
        res = []
        for k in SECTIONS:
            if k in out:
                b = out[k]
                b["newCount"], b["changedCount"] = len(b["new"]), len(b["changed"])
                b["new"], b["changed"] = b["new"][:300], b["changed"][:300]
                res.append(b)
        return res

    @r.post("/transfer/read")
    def transfer_read(file: UploadFile = File(...), user=Depends(admin), conn=Depends(get_db)):
        """Step 1 on the receiving laptop: read the file and show what would change (nothing is applied yet)."""
        TRANSFER_DIR.mkdir(parents=True, exist_ok=True)
        for old in TRANSFER_DIR.glob("*.zip"):                   # only the latest file waits to be applied
            old.unlink(missing_ok=True)
        token = _uuid.uuid4().hex
        f = TRANSFER_DIR / f"{token}.zip"
        with open(f, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        try:
            if not zipfile.is_zipfile(f) and (file.filename or "").lower().endswith((".csv", ".txt")):
                doc = inventory_csv_doc(f.read_bytes())            # an inventory list: kept as a setup ZIP to apply
                with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("setup.json", _json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"))
            if not zipfile.is_zipfile(f):
                raise HTTPException(422, "Choose a TresNinos_setup ZIP (Transfer -> Make a setup file) or an inventory list saved as CSV")
            with zipfile.ZipFile(f) as z:
                if "setup.json" not in z.namelist():
                    raise HTTPException(422, "This ZIP is not a Tres Niños setup file (no setup.json)")
                doc = _json.loads(z.read("setup.json").decode("utf-8"))
            if doc.get("app") != "tres-ninos-shop" or doc.get("kind") != "setup":
                raise HTTPException(422, "This file was not made by the Tres Niños system")
            return {"token": token, "made": doc.get("made"), "from": doc.get("from"), "by": doc.get("by"),
                    "sections": plan(conn, doc)}
        except HTTPException:
            f.unlink(missing_ok=True)
            raise
        except (ValueError, KeyError, zipfile.BadZipFile):
            f.unlink(missing_ok=True)
            raise HTTPException(422, "The setup file is damaged")

    class ApplyIn(BaseModel):
        token: str
        sections: list[str]
        updateExisting: bool = True
        stockCounts: bool = False

    def put_photo(z, arc, dest, box, fit=False):
        if not arc or not arc.startswith("photos/") or ".." in arc or arc not in z.namelist():
            return False
        try:
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(z.read(arc)))).convert("RGB")
        except Exception:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        (ImageOps.fit(im, box) if fit else im).save(dest, "JPEG", quality=88)
        return True

    def upsert(conn, table, where_sql, where_args, cols, update):
        """-> (id, 'new'|'updated'|'kept')"""
        cur = conn.execute(f"SELECT id FROM {table} WHERE {where_sql}", where_args).fetchone()
        if cur:
            if update and cols:
                conn.execute(f"UPDATE {table} SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*cols.values(), cur[0]))
                return cur[0], "updated"
            return cur[0], "kept"
        return conn.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                            tuple(cols.values())).lastrowid, "new"

    def pick(d, fields):
        return {k: d.get(k) for k in fields if k in d}

    def login_user(conn, username, name):
        """the sales person of a moved sale: the same username here, or a new phone login that cannot sign in yet
        (set its password in Cash Flow -> the employee -> Phone login)"""
        u = conn.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if u:
            return u[0]
        return conn.execute("""INSERT INTO users(username, display_name, password_hash, active, created, is_admin)
                               VALUES(?,?,?,1,?,0)""", (username, name or username, "!moved-no-password", now())).lastrowid

    def item_for(conn, it):
        """the item of a moved sale line by name; made (stock 0) when this laptop does not have it yet"""
        cur = conn.execute("SELECT id FROM items WHERE name=? COLLATE NOCASE", (it["item"],)).fetchone()
        if cur:
            return cur[0]
        sell = it.get("sell_price") or 0
        gone = now() if _re_mod.search(r"\(deleted\)(?: \d+)?$", it["item"] or "") else None   # deleted there: hidden here too
        return conn.execute("INSERT INTO items(name, unit, quantity, sell_price, cost_price, cost_auto, deleted_at) VALUES(?,?,0,?,?,?,?)",
                            (it["item"], it.get("unit") or "pcs", sell, shop_db.cost_of(it.get("cost_price"), sell),
                             0 if (it.get("cost_price") or 0) > 0 else 1, gone)).lastrowid

    @r.post("/transfer/apply")
    def transfer_apply(body: ApplyIn, user=Depends(admin), conn=Depends(get_db)):
        """Step 2: apply the ticked sections. Matching rows are updated (unless updateExisting is off), new rows added,
        nothing deleted. Logs (payments, attendance...) are added only when the same line is not there yet."""
        f, doc = load_setup(body.token)
        sec = doc.get("sections") or {}
        want = set(body.sections) & set(SECTIONS)
        upd = body.updateExisting
        res = {k: {"new": 0, "updated": 0, "kept": 0, "lines": 0} for k in want}
        count = lambda k, st: res[k].__setitem__(st, res[k][st] + 1)

        def add_lines(table, parent_col, pid, rows, fields, key_fields):
            """adds the log lines this row does not have yet. Counted, not merged: two equal payments on one day in the
            file are two lines here too (and applying the file again adds nothing)."""
            from collections import Counter
            have = Counter(k_(*[x[c] for c in key_fields]) for x in conn.execute(f"SELECT * FROM {table} WHERE {parent_col}=?", (pid,)))
            n = 0
            for x in rows:
                key = k_(*[x.get(c) for c in key_fields])
                if have[key] > 0:
                    have[key] -= 1
                    continue
                vals = pick(x, fields)
                conn.execute(f"INSERT INTO {table}({parent_col}, {', '.join(vals)}) VALUES(?, {', '.join('?' * len(vals))})",
                             (pid, *vals.values()))
                n += 1
            return n

        with zipfile.ZipFile(f) as z, conn:
            if "employees" in want:
                for name in (sec.get("employees") or {}).get("positions", []):
                    conn.execute("INSERT OR IGNORE INTO positions(name) VALUES(?)", (name,))
                for e in (sec.get("employees") or {}).get("rows", []):
                    eid, st = upsert(conn, "employees", "name=? COLLATE NOCASE", (e["name"],),
                                     {"name": e["name"], **pick(e, EMP_F)}, upd)
                    if st == "new":
                        conn.execute("UPDATE employees SET created=? WHERE id=?", (now(), eid))
                    count("employees", st)
                    lg = e.get("login") or {}
                    if lg.get("username") and not conn.execute("SELECT user_id FROM employees WHERE id=?", (eid,)).fetchone()[0]:
                        uid = login_user(conn, lg["username"], lg.get("name"))
                        if not conn.execute("SELECT 1 FROM employees WHERE user_id=?", (uid,)).fetchone():
                            conn.execute("UPDATE employees SET user_id=? WHERE id=?", (uid, eid))
                    # a STARTER file (made for a new shop) may carry the phone password, only as a PBKDF2 hash; it is
                    # used only for a phone login that has no password yet. Normal setup files never carry one.
                    ph = lg.get("password_hash") or ""
                    if lg.get("username") and _re_mod.fullmatch(r"pbkdf2\$[0-9a-f]{32}\$[0-9a-f]{64}", ph):
                        conn.execute("""UPDATE users SET password_hash=? WHERE username=? COLLATE NOCASE AND COALESCE(is_admin,0)=0
                                        AND password_hash='!moved-no-password'""", (ph, lg["username"]))
                    if e.get("photo") and (st == "new" or upd):
                        if put_photo(z, e["photo"], AVATAR_DIR / f"emp{eid}.jpg", (256, 256), fit=True):
                            conn.execute("UPDATE employees SET photo=? WHERE id=?", (f"emp{eid}.jpg", eid))
                    lines = 0
                    for a in e.get("attendance") or []:
                        lines += conn.execute("INSERT OR IGNORE INTO attendance(employee_id,date,time_in) VALUES(?,?,?)",
                                              (eid, a["date"], a["time_in"])).rowcount
                    for h in e.get("hours") or []:
                        lines += conn.execute("INSERT OR IGNORE INTO work_hours(employee_id,date,ot_hours,ut_hours,note) VALUES(?,?,?,?,?)",
                                              (eid, h["date"], h["ot_hours"], h["ut_hours"], h.get("note"))).rowcount
                    lines += add_lines("employee_cash_advances", "employee_id", eid, e.get("advances") or [], ["amount", "note", "date"], ["date", "amount", "note"])
                    lines += add_lines("employee_payments", "employee_id", eid, e.get("payments") or [], ["kind", "amount", "note", "date"], ["kind", "date", "amount", "note"])
                    res["employees"]["lines"] += lines
            if "obligations" in want:
                for o in sec.get("obligations") or []:
                    oid, st = upsert(conn, "obligations", "name=? COLLATE NOCASE AND kind=?", (o["name"], o["kind"]),
                                     {"kind": o["kind"], "name": o["name"], **pick(o, OBL_F)}, upd)
                    count("obligations", st)
                    res["obligations"]["lines"] += add_lines("obligation_payments", "obligation_id", oid, o.get("payments") or [],
                                                             ["amount", "note", "date"], ["date", "amount"])
            if "maintenance" in want:
                for m in sec.get("maintenance") or []:
                    mid, st = upsert(conn, "maintenance_items", "name=? COLLATE NOCASE", (m["name"],), {"name": m["name"], **pick(m, MNT_F)}, upd)
                    count("maintenance", st)
                    res["maintenance"]["lines"] += add_lines("maintenance_payments", "item_id", mid, m.get("payments") or [],
                                                             ["amount", "date"], ["date", "amount"])
            if "benefits" in want:
                for b in sec.get("benefits") or []:
                    conn.execute("INSERT OR IGNORE INTO custom_types(label) VALUES(?)", (b["label"],))
                    tid = conn.execute("SELECT id FROM custom_types WHERE label=? COLLATE NOCASE", (b["label"],)).fetchone()[0]
                    for c in b["items"]:
                        _, st = upsert(conn, "custom_items", "custom_type_id=? AND name=? COLLATE NOCASE", (tid, c["name"]),
                                       {"custom_type_id": tid, "name": c["name"], **pick(c, CUS_F)}, upd)
                        count("benefits", st)
            if "supply" in want:
                for u in (sec.get("supply") or {}).get("units", []):
                    conn.execute("INSERT OR IGNORE INTO units(name, to_kg) VALUES(?,?)", (u["name"], u.get("to_kg")))
                for p_ in (sec.get("supply") or {}).get("products", []):
                    pid = None
                    if p_.get("name"):
                        conn.execute("INSERT OR IGNORE INTO products(name, icon_key, created) VALUES(?,?,?)", (p_["name"], p_.get("icon_key"), now()))
                        pid = conn.execute("SELECT id FROM products WHERE name=? COLLATE NOCASE", (p_["name"],)).fetchone()[0]
                        if p_.get("photo") and put_photo(z, p_["photo"], PRODUCT_DIR / f"{pid}.jpg", (600, 600)):
                            conn.execute("UPDATE products SET image=? WHERE id=?", (f"{pid}.jpg", pid))
                    for i in p_["items"]:
                        cur = conn.execute("SELECT id, sell_price FROM items WHERE name=? COLLATE NOCASE", (i["name"],)).fetchone()
                        cols = pick(i, ITEM_F)
                        if "cost_price" in cols and not (cols["cost_price"] or 0) > 0:     # no cost in the file: 65% of sell
                            cols["cost_price"] = shop_db.assumed_cost(cols.get("sell_price", cur["sell_price"] if cur else 0))
                            cols["cost_auto"] = 1
                        if pid:
                            cols["product_id"] = pid
                        if cur:
                            if upd:
                                if body.stockCounts:
                                    cols["quantity"] = i.get("quantity") or 0
                                conn.execute(f"UPDATE items SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*cols.values(), cur[0]))
                                count("supply", "updated")
                            else:
                                count("supply", "kept")
                            iid = cur[0]
                        else:
                            sell = i.get("sell_price") or 0
                            cols.update(name=i["name"], quantity=i.get("quantity") or 0,
                                        unit=i.get("unit") or "pcs", sell_price=sell, cost_price=shop_db.cost_of(i.get("cost_price"), sell))
                            if not (i.get("cost_price") or 0) > 0:
                                cols["cost_auto"] = 1
                            iid = conn.execute(f"INSERT INTO items({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", tuple(cols.values())).lastrowid
                            count("supply", "new")
                        for so in i.get("sourcing") or []:
                            conn.execute("""INSERT INTO supply_sourcing(item_id,source,unit_cost,transfer_cost,maintenance_cost,updated)
                                            VALUES(?,?,?,?,?,?) ON CONFLICT(item_id, source) DO UPDATE SET unit_cost=excluded.unit_cost,
                                            transfer_cost=excluded.transfer_cost, maintenance_cost=excluded.maintenance_cost""",
                                         (iid, so["source"], so["unit_cost"], so["transfer_cost"], so["maintenance_cost"], now()))
            if "trucks" in want:
                for t in sec.get("trucks") or []:
                    _, st = upsert(conn, "transports", "truck_name=? COLLATE NOCASE AND COALESCE(plate,'')=COALESCE(?,'')",
                                   (t["truck_name"], t.get("plate")), {"truck_name": t["truck_name"], "plate": t.get("plate"), **pick(t, TRK_F)}, upd)
                    count("trucks", st)
            if "orders" in want:
                for o in sec.get("orders") or []:
                    oid, st = upsert(conn, "bulk_orders", "customer=? COLLATE NOCASE AND COALESCE(created,'')=COALESCE(?,'')",
                                     (o["customer"], o.get("created")), {"customer": o["customer"], "created": o.get("created"), **pick(o, ORD_F)}, upd)
                    count("orders", st)
                    if st == "new":
                        for i in o.get("items") or []:
                            it = conn.execute("SELECT id FROM items WHERE name=? COLLATE NOCASE", (i.get("item"),)).fetchone() if i.get("item") else None
                            conn.execute("""INSERT INTO bulk_order_items(order_id,name,qty,unit,price,delivered,item_id,kg_per_unit,stock_taken)
                                            VALUES(?,?,?,?,?,?,?,?,?)""", (oid, i["name"], i["qty"], i.get("unit"), i["price"], i.get("delivered") or 0,
                                                                           it[0] if it else None, i.get("kg_per_unit"),
                                                                           i.get("stock_taken") if it else None))
                    # deliveries (Planner): a run here with the same day + truck is the same run. Runs the order does not
                    # have yet are added; with "update existing" the crew and the delivered tick follow the file
                    emp_id = lambda nm: (conn.execute("SELECT id FROM employees WHERE name=? COLLATE NOCASE", (nm,)).fetchone() or [None])[0]
                    here = runs_here(conn, oid)
                    for x in o.get("runs") or []:
                        crew = (emp_id(x["driver"]) if x.get("driver") else None,
                                [i for i in (emp_id(h) for h in x.get("helpers") or []) if i])
                        same = here.get(file_run_key(x))
                        if same:
                            run = same.pop(0)
                            if upd and st != "new":
                                conn.execute("UPDATE bulk_order_runs SET trips=?, done=? WHERE id=?", (x.get("trips") or 1, x.get("done") or 0, run["id"]))
                                set_crew(conn, run["id"], *crew)
                                res["orders"]["lines"] += 1
                            continue
                        tr = conn.execute("SELECT id FROM transports WHERE truck_name=? COLLATE NOCASE AND COALESCE(plate,'')=COALESCE(?,'')",
                                          (x.get("truck"), x.get("plate"))).fetchone()
                        mode_ = x.get("mode") if x.get("mode") in RUN_MODES else "truck"
                        run_id = conn.execute("""INSERT INTO bulk_order_runs(order_id,transport_id,trips,date,done,mode,carrier,carrier_cost)
                                                 VALUES(?,?,?,?,?,?,?,?)""",
                                              (oid, tr[0] if tr and mode_ == "truck" else None, x.get("trips") or 1, x.get("date"),
                                               x.get("done") or 0, mode_, x.get("carrier"), x.get("carrier_cost"))).lastrowid
                        set_crew(conn, run_id, *crew)
                        if st != "new":
                            res["orders"]["lines"] += 1
                    res["orders"]["lines"] += add_lines("bulk_order_payments", "order_id", oid, o.get("payments") or [],
                                                        ["amount", "note", "date"], ["date", "amount"])
            if "banks" in want:
                for a in sec.get("banks") or []:
                    if a.get("bank"):
                        aid, st = upsert(conn, "pay_accounts", """bank=? COLLATE NOCASE AND COALESCE(nickname,'')=COALESCE(?,'')
                                         AND COALESCE(last4,'')=COALESCE(?,'')""", (a["bank"], a.get("nickname"), a.get("last4")),
                                         {"bank": a["bank"], "nickname": a.get("nickname"), "last4": a.get("last4"), **pick(a, BANK_F)}, upd)
                        if st == "new":
                            conn.execute("UPDATE pay_accounts SET created=? WHERE id=?", (now(), aid))
                        if a.get("sales_bank") and (upd or not shop_db.get_setting(conn, "sales_bank_account")):
                            shop_db.set_setting(conn, "sales_bank_account", str(aid))
                        count("banks", st)
                        have = {k_(x["date"], x["amount"], x["direction"], x["ref"]) for x in conn.execute("SELECT * FROM pay_entries WHERE account_id=?", (aid,))}
                        key = lambda e: k_(e["date"], e["amount"], e["direction"], e.get("ref"))
                    else:
                        aid = None
                        have = line_keys(conn)              # counted: two equal payments on one day are two lines
                        key = line_key
                    for e in a.get("entries") or []:
                        if aid is None:
                            if have[key(e)] > 0:
                                have[key(e)] -= 1
                                continue
                        elif key(e) in have:
                            continue
                        oid = opid = None
                        if e.get("order"):
                            o = conn.execute("SELECT id FROM bulk_orders WHERE customer=? COLLATE NOCASE AND COALESCE(created,'')=COALESCE(?,'')",
                                             tuple(e["order"])).fetchone()
                            if o:
                                oid = o[0]
                                p_ = conn.execute("""SELECT p.id FROM bulk_order_payments p WHERE p.order_id=? AND p.date=? AND p.amount=?
                                                     AND NOT EXISTS (SELECT 1 FROM pay_entries x WHERE x.order_payment_id=p.id)""",
                                                  (oid, e["date"], e["amount"])).fetchone()
                                opid = p_[0] if p_ else None
                        m_ = e.get("method") if e.get("method") in ("cash", "bank") else ("bank" if aid else "cash")
                        conn.execute("""INSERT INTO pay_entries(account_id,method,direction,amount,date,party,ref,note,order_id,order_payment_id,created)
                                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (aid, m_, e["direction"], e["amount"], e["date"], e.get("party"),
                                                                         e.get("ref"), e.get("note"), oid, opid, now()))
                        if aid is not None:
                            have.add(key(e))
                        res["banks"]["lines"] += 1
            if "cash" in want:
                have = {k_(x["date"], x["direction"], x["amount"], x["note"]) for x in conn.execute("SELECT * FROM cash_entries")}
                for c in sec.get("cash") or []:
                    if k_(c["date"], c["direction"], c["amount"], c.get("note")) in have:
                        count("cash", "kept")
                        continue
                    conn.execute("INSERT INTO cash_entries(date,direction,amount,note) VALUES(?,?,?,?)", (c["date"], c["direction"], c["amount"], c.get("note")))
                    count("cash", "new")
            if "sales" in want:
                have = sales_here(conn)
                for sa in sec.get("sales") or []:
                    person = sa.get("person") or {}
                    key = k_(sa["created"], round(sa["total"] or 0, 2), person.get("username"))
                    mine = have.get(key)
                    if mine:                                   # already here (two equal sales in one second: both counted)
                        sid, mode = mine.pop(0)
                        if upd and pay_changed(sa, mode):
                            conn.execute("UPDATE sales SET pay_mode=?, pay_note=? WHERE id=?", (sa["pay_mode"], sa.get("pay_note"), sid))
                            count("sales", "updated")
                        else:
                            count("sales", "kept")
                        continue
                    uid = login_user(conn, person.get("username"), person.get("name")) if person.get("username") else None
                    sid = conn.execute("INSERT INTO sales(created,image,total,profit,user_id,pay_mode,pay_note) VALUES(?,?,?,?,?,?,?)",
                                       (sa["created"], None, sa["total"], sa.get("profit"), uid,
                                        sa.get("pay_mode") if sa.get("pay_mode") in ("cash", "bank") else None, sa.get("pay_note"))).lastrowid
                    for it in sa.get("items") or []:
                        iid = item_for(conn, it)
                        conn.execute("INSERT INTO sale_items(sale_id,item_id,qty,sell_price,cost_price) VALUES(?,?,?,?,?)",
                                     (sid, iid, it["qty"], it["sell_price"], it["cost_price"]))
                        res["sales"]["lines"] += 1
                    count("sales", "new")
            if "drafts" in want:
                for x in sec.get("drafts") or []:
                    if draft_here(conn, x):
                        count("drafts", "kept")
                        continue
                    person = x.get("person") or {}
                    uid = login_user(conn, person["username"], person.get("name")) if person.get("username") else None
                    rid, rc = None, x.get("receipt")
                    if rc and uid:                                # the sales person's receipt, already read
                        rid = conn.execute("""INSERT INTO receipts(user_id, client_id, image, status, created, finished, seconds, device,
                                              model, pay_mode, pay_note, pay_sure) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                           (uid, None, "", "done", rc.get("created") or x.get("created") or now(), rc.get("finished"),
                                            rc.get("seconds"), rc.get("device"), rc.get("model"), rc.get("pay_mode"), rc.get("pay_note"),
                                            rc.get("pay_sure"))).lastrowid
                        if x.get("photo") and put_photo(z, x["photo"], UPLOAD_DIR / f"{rid}.jpg", (0, 0)):
                            conn.execute("UPDATE receipts SET image=? WHERE id=?", (str(UPLOAD_DIR / f"{rid}.jpg"), rid))
                        for l_ in rc.get("lines") or []:
                            it = conn.execute("SELECT id FROM items WHERE name=? COLLATE NOCASE", (l_.get("item"),)).fetchone()
                            if it:
                                conn.execute("""INSERT INTO receipt_lines(receipt_id, ocr_text, item_id, score, qty, verified)
                                                VALUES(?,?,?,?,?,?)""", (rid, l_.get("ocr_text"), it[0], l_.get("score"), l_.get("qty"),
                                                                         1 if l_.get("verified") else 0))
                        for m_ in rc.get("unmatched") or []:
                            conn.execute("INSERT INTO receipt_unmatched(receipt_id, ocr_text, item_text, qty) VALUES(?,?,?,?)",
                                         (rid, m_.get("ocr_text"), m_.get("item_text"), m_.get("qty")))
                    data = x.get("data") or {}
                    for row in data.get("rows") or []:            # item ids are different on this laptop: by name
                        it = conn.execute("SELECT id FROM items WHERE name=? COLLATE NOCASE", (row.get("name"),)).fetchone()
                        row["itemId"] = it[0] if it else None
                    conn.execute("""INSERT INTO delivery_drafts(receipt_id, user_id, created, status, customer, address, delivery_date, data)
                                    VALUES(?,?,?,'new',?,?,?,?)""", (rid, uid, x.get("created") or now(), x.get("customer"), x.get("address"),
                                                                    x.get("delivery_date"), _json_mod.dumps(data)))
                    count("drafts", "new")
        f.unlink(missing_ok=True)
        note(conn, user, "Transfer applied", f"from {doc.get('from') or 'another laptop'}: " +
             ", ".join(f"{k} +{v['new']}/~{v['updated']}" for k, v in res.items())[:250])
        return {"applied": res, "from": doc.get("from"), "made": doc.get("made")}

    # ================= DELETE ALL RECORDS (Transfer page, red card) =================
    # For a fresh start (after trying the mock data, or before giving the laptop to another shop). Needs the system
    # password + YOUR OWN username and password. First a full copy goes to backups/before_delete_all_<time>/ (database
    # + photos), so a mistake can still be undone by copying it back. Kept: the desktop accounts and the system password.
    class WipeIn(BaseModel):
        systemPassword: str
        username: str
        password: str
        keepPhoneLogins: bool = False

    @r.post("/danger/delete-all")
    def delete_all(body: WipeIn, request: Request, user=Depends(admin), conn=Depends(get_db)):
        if reauth is None:
            raise HTTPException(503, "Not available")
        reauth(request, conn, user, body.systemPassword, body.username, body.password)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        bdir = BASE / "backups" / f"before_delete_all_{stamp}"
        bdir.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(bdir / "inventory.db"))
        conn.backup(dst)                                         # consistent copy even while the server runs
        dst.close()
        phone_users = [] if body.keepPhoneLogins else conn.execute(
            "SELECT id, username, avatar FROM users WHERE COALESCE(is_admin,0)=0").fetchall()
        tables = [x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if x[0] not in ("users", "sessions", "settings") and not x[0].startswith("sqlite_")]
        with conn:
            for t in tables:
                conn.execute(f'DELETE FROM "{t}"')
            for u in phone_users:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
                conn.execute("DELETE FROM users WHERE id=?", (u["id"],))
            conn.execute("DELETE FROM sqlite_sequence WHERE name NOT IN ('users')")
            conn.executemany("INSERT OR IGNORE INTO units(name, to_kg) VALUES(?,?)", shop_db.DEFAULT_UNITS)
        # photos: moved (not deleted) into the backup folder
        moved = []
        for folder in (UPLOAD_DIR, PRODUCT_DIR, PAY_DIR, VIEW_DIR, TRANSFER_DIR):
            try:
                if folder.exists():
                    shutil.move(str(folder), str(bdir / folder.name))
                    moved.append(folder.name)
            except OSError:
                pass
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        av_dest = bdir / "avatars"
        for f in list(AVATAR_DIR.glob("emp*.jpg")) + [AVATAR_DIR / u["avatar"] for u in phone_users if u["avatar"]] + \
                [AVATAR_DIR / f"{u['username'].lower()}.jpg" for u in phone_users]:
            try:
                if f.is_file():
                    av_dest.mkdir(exist_ok=True)
                    shutil.move(str(f), str(av_dest / f.name))
            except OSError:
                pass
        with conn:
            shop_db.audit(conn, user["id"], "Deleted all records", f"a copy was saved first in {bdir}")
        try:
            conn.execute("VACUUM")                               # give the space back
        except sqlite3.Error:
            pass
        return {"ok": True, "backup": str(bdir), "tables": len(tables), "phoneLoginsRemoved": len(phone_users)}

    return r
