"""
shop_db.py  =  every table that is NOT part of the OCR/receipt core.

It extends the SAME inventory.db that ocr_engine.py and server.py already use (one database, one file).
Call ensure_schema(conn) once after connecting. It is safe to call every start-up:
tables use CREATE TABLE IF NOT EXISTS, new columns on old tables are added by a migration.

Groups
  SUPPLY    products (+ items = one row per variant/spec, already used by the OCR), stock_purchases, supply_sourcing
  PEOPLE    employees (payroll profile + photo), positions, employee_cash_advances, employee_payments
  CASHFLOW  obligations (loan | payment), obligation_payments, maintenance_items, custom_types, custom_items
  ATTENDANCE/CASH  attendance, cash_entries, maintenance_payments
  ORDERS    transports, bulk_orders, bulk_order_items, bulk_order_payments, bulk_order_runs, run_crew, units,
            delivery_drafts (delivery receipts read from the phones, waiting for the owner)
  PAYMENTS  pay_entries (every payment: cash or bank); pay_accounts is kept for old data only (no bank linking)
  DASHBOARD priority_dismissed
  SYSTEM    settings (system password), user_activity (who was in charge each day), audit_log (what they did)
  PHOTOS    the receipt label (111_30) is computed in code (see receipt_label), nothing extra to store

Your original diagram -> these tables
  supply            -> items (one running quantity per variant) + stock_purchases (every buy, with savings)
  supply_sourcing   -> supply_sourcing  (iexc/eexc = unit_cost, tiexc/teexc = transfer_cost, ma_iexc/ma_eexc = maintenance_cost;
                                         one row per item per source internal/external)
  Sales_person      -> users (phone login) + employees (photo) + sales + sale_items + receipts + receipt_lines
  salaries          -> employees + employee_cash_advances + employee_payments (kind payroll | bonus)
  operational_expenses -> maintenance_items (consumer = name, duration = start_date, maintainance = amount,
                                             maintainance_duration = freq_value + freq_unit) + maintenance_payments
  Expenditure       -> obligations (loan | payment: initial_price = principal, interest = interest_rate,
                                    final_price = final_payment, payment_time = freq, duration_time = start/end) + obligation_payments
"""
import sqlite3


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _add_col(conn, table, name, decl):
    if name not in _cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


SCHEMA = """
-- ============ SUPPLY ============
-- items (created by ocr_engine.init_db) stays the stock table: ONE running quantity per row.
-- A "product" groups its variants (Portland Cement -> 40kg bag, 25kg bag). Icon is a lucide-react name.
CREATE TABLE IF NOT EXISTS products (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    icon_key TEXT,
    created  TEXT
);

-- Every time stock is bought. Does NOT track batches: it only logs history and the saving vs the standard cost.
--   standard_cost = items.cost_price at that moment (the benchmark, e.g. 30 per kilo)
--   unit_cost     = what was really paid (e.g. 25)
--   savings       = (standard_cost - unit_cost) * qty   -> extra profit when positive, overspend when negative
CREATE TABLE IF NOT EXISTS stock_purchases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(id),
    qty           REAL NOT NULL CHECK (qty > 0),
    unit_cost     REAL NOT NULL,
    standard_cost REAL NOT NULL,
    savings       REAL NOT NULL,
    source        TEXT NOT NULL DEFAULT 'external' CHECK (source IN ('internal','external')),
    supplier      TEXT,
    bought_at     TEXT NOT NULL,
    note          TEXT
);

-- Cost of getting an item from each source (the old iexc/eexc/tiexc/teexc/ma_iexc/ma_eexc columns, one row per source).
--   internal = we make/quarry it ourselves, external = we buy it from an outside supplier
--   unit_cost        = iexc / eexc    (extraction cost or purchase cost per unit)
--   transfer_cost    = tiexc / teexc  (hauling / freight per unit)
--   maintenance_cost = ma_iexc / ma_eexc (upkeep or holding cost per unit)
CREATE TABLE IF NOT EXISTS supply_sourcing (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id          INTEGER NOT NULL REFERENCES items(id),
    source           TEXT NOT NULL CHECK (source IN ('internal','external')),
    unit_cost        REAL NOT NULL DEFAULT 0,
    transfer_cost    REAL NOT NULL DEFAULT 0,
    maintenance_cost REAL NOT NULL DEFAULT 0,
    updated          TEXT,
    UNIQUE(item_id, source)
);

-- ============ PEOPLE ============
CREATE TABLE IF NOT EXISTS positions (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

-- Payroll profile. user_id links to users.id when this person also logs in on the phone (sales person):
-- the photo set here is the one the phone loads at login and the Sales page shows.
CREATE TABLE IF NOT EXISTS employees (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    position       TEXT,
    rate_per_hour  REAL NOT NULL DEFAULT 0,
    per_day_rate   REAL NOT NULL DEFAULT 0,
    present_days   REAL NOT NULL DEFAULT 0,
    absent_days    REAL NOT NULL DEFAULT 0,
    overtime_hours REAL NOT NULL DEFAULT 0,
    notes          TEXT,
    photo          TEXT,                                  -- file name inside avatars/
    user_id        INTEGER UNIQUE REFERENCES users(id),
    active         INTEGER NOT NULL DEFAULT 1,
    created        TEXT
);
CREATE TABLE IF NOT EXISTS employee_cash_advances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    amount      REAL NOT NULL, note TEXT, date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employee_payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'payroll' CHECK (kind IN ('payroll','bonus')),
    amount      REAL NOT NULL, note TEXT, date TEXT NOT NULL
);

-- ============ CASHFLOW ============
-- loan and payment share one table: a loan simply has an interest_rate. final_payment is the TOTAL payable.
CREATE TABLE IF NOT EXISTS obligations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL CHECK (kind IN ('loan','payment')),
    name          TEXT NOT NULL,
    party         TEXT,                                   -- lender or payee
    start_date    TEXT, end_date TEXT,
    freq_value    INTEGER NOT NULL DEFAULT 1,
    freq_unit     TEXT NOT NULL DEFAULT 'months' CHECK (freq_unit IN ('days','weeks','months')),
    principal     REAL NOT NULL DEFAULT 0,
    interest_rate REAL NOT NULL DEFAULT 0,
    final_payment REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','completed')),
    created       TEXT
);
CREATE TABLE IF NOT EXISTS obligation_payments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    obligation_id INTEGER NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
    amount        REAL NOT NULL, note TEXT, date TEXT NOT NULL
);
-- maintenance_items also covers recurring running costs (electricity, rent...): name = what is paid for, frequency = how often
CREATE TABLE IF NOT EXISTS maintenance_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,                             -- the name describes itself ("Aircon Cleaning")
    amount     REAL NOT NULL DEFAULT 0,
    freq_value INTEGER NOT NULL DEFAULT 1,
    freq_unit  TEXT NOT NULL DEFAULT 'months' CHECK (freq_unit IN ('days','weeks','months')),
    last_paid  TEXT, notes TEXT
);
-- user-made categories such as PhilHealth / SSS / Pag-IBIG: always a MONTHLY cost
CREATE TABLE IF NOT EXISTS custom_types (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS custom_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    custom_type_id INTEGER NOT NULL REFERENCES custom_types(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    amount         REAL NOT NULL DEFAULT 0,
    basis          TEXT NOT NULL DEFAULT 'per_employee' CHECK (basis IN ('per_employee','flat')),
    notes          TEXT
);
-- ============ ATTENDANCE + CASH ============
-- one row per employee per day = checked in. Checking in adds 1 to employees.present_days (unchecking removes it).
CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,                          -- YYYY-MM-DD
    time_in     TEXT NOT NULL,                          -- HH:MM, 24-hour
    UNIQUE(employee_id, date)
);
-- overtime / undertime hours of one employee on one day (Dashboard -> Overtime, Payslip). Setting them also keeps
-- employees.overtime_hours / undertime_hours (the Cash Flow totals of the pay cycle) in step.
CREATE TABLE IF NOT EXISTS work_hours (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    ot_hours    REAL NOT NULL DEFAULT 0,
    ut_hours    REAL NOT NULL DEFAULT 0,
    note        TEXT,
    UNIQUE(employee_id, date)
);
-- money you move by hand: opening balance, deposits, withdrawals, anything not recorded elsewhere
CREATE TABLE IF NOT EXISTS cash_entries (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('in','out')),
    amount    REAL NOT NULL CHECK (amount > 0),
    note      TEXT
);
-- each time a maintenance item is marked paid (so Cash on Hand can subtract it)
CREATE TABLE IF NOT EXISTS maintenance_payments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES maintenance_items(id) ON DELETE CASCADE,
    amount  REAL NOT NULL, date TEXT NOT NULL
);

-- ============ BULK / VIP ORDERS (Sales page, right column) ============
-- saved trucks, reused on every order
CREATE TABLE IF NOT EXISTS transports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    truck_name TEXT NOT NULL,
    plate      TEXT,
    capacity   TEXT,
    cost       REAL NOT NULL DEFAULT 0,                  -- cost per delivery trip
    created    TEXT
);
-- one big order from a client. status: active -> done (moves to Order History)
CREATE TABLE IF NOT EXISTS bulk_orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer       TEXT NOT NULL,
    start_date     TEXT, end_date TEXT,                   -- target delivery window
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','done')),
    completed_date TEXT,
    created        TEXT
);
-- the order total is never stored: it is SUM(qty * price) of these rows
CREATE TABLE IF NOT EXISTS bulk_order_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id  INTEGER NOT NULL REFERENCES bulk_orders(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    qty       REAL NOT NULL DEFAULT 0,
    unit      TEXT,
    price     REAL NOT NULL DEFAULT 0,                    -- price per unit
    delivered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bulk_order_payments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES bulk_orders(id) ON DELETE CASCADE,
    amount   REAL NOT NULL, note TEXT, date TEXT NOT NULL
);
-- one row per truck leg: which truck, how many return trips, scheduled date. order_id NULL = a truck trip of its own
-- (Planner: deliver something, get materials, personal use) - see purpose / place / note / time (round 18)
CREATE TABLE IF NOT EXISTS bulk_order_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     INTEGER REFERENCES bulk_orders(id) ON DELETE CASCADE,
    transport_id INTEGER REFERENCES transports(id),
    trips        INTEGER NOT NULL DEFAULT 1,
    date         TEXT
);
-- the crew of one truck run: the driver is bulk_order_runs.driver_id, the helpers (labor / temporary) are here
CREATE TABLE IF NOT EXISTS run_crew (
    run_id      INTEGER NOT NULL REFERENCES bulk_order_runs(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, employee_id)
);
-- a receipt photo from a phone that the reader recognised as a DELIVERY (words like "delivery", "deliver to",
-- "D.R. No."): the owner gets a ready-filled order to check and apply (Sales counter bubble + dashboard Priority)
CREATE TABLE IF NOT EXISTS delivery_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id    INTEGER UNIQUE,
    user_id       INTEGER,
    created       TEXT,
    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','done','dismissed')),
    customer      TEXT, address TEXT, delivery_date TEXT,
    data          TEXT,                                    -- JSON: rows, unmatched, the words that were found
    order_id      INTEGER,
    handled       TEXT
);

-- ============ PAYMENTS (Payments page): the shop's bank / e-wallet accounts kept like a passbook ============
-- NEVER stored: card or ATM numbers, CVV, PIN, online-banking usernames or passwords. The shop system does not connect
-- to any bank; it only records what the owner sees in the bank app. An account = bank name + nickname + last 4 digits.
CREATE TABLE IF NOT EXISTS pay_accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bank         TEXT NOT NULL,                           -- BDO, GCash, Landbank, ChinaBank ... or any name typed
    nickname     TEXT, holder TEXT,
    last4        TEXT,                                    -- optional, at most 4 digits (to tell two accounts apart)
    opening      REAL NOT NULL DEFAULT 0,                 -- balance when the shop started recording here
    opening_date TEXT,
    created      TEXT
);
-- one money movement. account_id NULL = a CASH payment (kept with its receipt photo).
-- order_id + order_payment_id: a payment for a Sales -> Add Order order; it also writes the bulk_order_payments row,
-- so the order in Sales shows it as paid (the Sales page itself is unchanged).
CREATE TABLE IF NOT EXISTS pay_entries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id       INTEGER REFERENCES pay_accounts(id),
    direction        TEXT NOT NULL CHECK (direction IN ('in','out')),
    amount           REAL NOT NULL CHECK (amount > 0),
    date             TEXT NOT NULL,
    party            TEXT, ref TEXT, note TEXT,           -- who paid / was paid, reference no. from the bank app
    order_id         INTEGER REFERENCES bulk_orders(id),
    order_payment_id INTEGER,
    photo            TEXT,                                -- receipt photo inside payment_photos/
    created          TEXT
);
CREATE INDEX IF NOT EXISTS pay_entries_account ON pay_entries(account_id, date);

-- ============ UNITS (drop-down lists: order items, truck capacity) ============
-- to_kg = how many kg one unit weighs when that is always true (kg, metric ton). NULL for sacks, pcs ... (their weight
-- comes from the item, e.g. "40kg bag"). Used to suggest how many truck trips an order needs.
CREATE TABLE IF NOT EXISTS units (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    to_kg REAL
);

-- ============ SYSTEM / WHO WAS IN CHARGE ============
-- key/value settings. system_password = PBKDF2 hash of the shop's system password (whoever knows it can make an account)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- one row per account per day it was used: first and last request of that day (= "in charge" on the Calendar page)
CREATE TABLE IF NOT EXISTS user_activity (
    user_id    INTEGER NOT NULL REFERENCES users(id),
    date       TEXT NOT NULL,                             -- YYYY-MM-DD
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (user_id, date)
);
-- what each desktop user did (sign in, stock bought or corrected, prices, payments...), shown per day in Calendar
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id),
    action  TEXT NOT NULL,
    detail  TEXT
);

-- ============ DASHBOARD ============
-- Priority cards are computed from the data (low stock, bills due...). Pressing X stores the card key here.
CREATE TABLE IF NOT EXISTS priority_dismissed (
    key          TEXT PRIMARY KEY,
    dismissed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_receipts_user_created ON receipts(user_id, created);
CREATE INDEX IF NOT EXISTS idx_sales_user_created    ON sales(user_id, created);
CREATE INDEX IF NOT EXISTS idx_purchases_item        ON stock_purchases(item_id, bought_at);
CREATE INDEX IF NOT EXISTS idx_order_items           ON bulk_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_payments        ON bulk_order_payments(order_id);
CREATE INDEX IF NOT EXISTS idx_audit_at              ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_activity_date         ON user_activity(date);
"""

# receipts / receipt_lines are created by server.py, but the indexes above need them to exist first.
RECEIPT_STUBS = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    display_name TEXT, password_hash TEXT NOT NULL, avatar TEXT, active INTEGER NOT NULL DEFAULT 1, created TEXT
);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, client_id TEXT, image TEXT NOT NULL,
    status TEXT NOT NULL, error TEXT, created TEXT, started TEXT, finished TEXT, seconds REAL, device TEXT, model TEXT,
    UNIQUE(user_id, client_id)
);
CREATE TABLE IF NOT EXISTS receipt_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id INTEGER NOT NULL, ocr_text TEXT, item_id INTEGER NOT NULL,
    score REAL, qty REAL, verified INTEGER NOT NULL DEFAULT 0, sold INTEGER NOT NULL DEFAULT 0, sale_id INTEGER
);
CREATE TABLE IF NOT EXISTS receipt_unmatched (
    id INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id INTEGER NOT NULL, ocr_text TEXT, item_text TEXT, qty REAL
);
"""


def ensure_schema(conn):
    conn.executescript(RECEIPT_STUBS)
    conn.executescript(SCHEMA)
    # migrations on tables that existed before this file
    _add_col(conn, "items", "product_id", "INTEGER REFERENCES products(id)")
    _add_col(conn, "items", "spec", "TEXT")          # e.g. "40kg bag" (the variant label shown in Supply)
    _add_col(conn, "users", "is_admin", "INTEGER NOT NULL DEFAULT 0")   # owner accounts may use /api/shop/*
    _add_col(conn, "maintenance_items", "start_date", "TEXT")           # when the running cost starts (first due date)
    _add_col(conn, "products", "image", "TEXT")                         # photo file name inside product_images/
    # A-B-C stock alert levels per variant: stock <= level_a = A (critical), <= level_b = B (low), <= level_c = C (watch)
    _add_col(conn, "items", "level_a", "REAL")
    _add_col(conn, "items", "level_b", "REAL")
    _add_col(conn, "items", "level_c", "REAL")
    _add_col(conn, "transports", "capacity_value", "REAL")                 # 4      (truck capacity as a number ...)
    _add_col(conn, "transports", "capacity_unit", "TEXT")                  # metric tons  (... and a unit from `units`)
    _add_col(conn, "bulk_order_items", "item_id", "INTEGER REFERENCES items(id)")   # picked from the inventory (optional)
    _add_col(conn, "bulk_order_items", "kg_per_unit", "REAL")
    _add_col(conn, "employees", "temporary", "INTEGER NOT NULL DEFAULT 0")     # extra help hired for a few days
    _add_col(conn, "bulk_order_runs", "driver_id", "INTEGER REFERENCES employees(id)")   # who drives this run
    _add_col(conn, "bulk_order_runs", "done", "INTEGER NOT NULL DEFAULT 0")              # this trip was delivered
    _add_col(conn, "bulk_orders", "address", "TEXT")                                     # where to deliver
    _add_col(conn, "employees", "undertime_hours", "REAL NOT NULL DEFAULT 0")  # Cash Flow: hours short this cycle
    _add_col(conn, "employees", "removed", "TEXT")          # the day the person was removed (hidden, not deleted)
    # how a sale was paid: 'cash' or 'bank' (GCash / Maya / InstaPay count as bank).
    # Since round 17 the sales person picks Cash or Bank on the phone when pressing Update (before: read from the
    # photo). NULL = not chosen yet (or a sale made before this existed) = cash
    _add_col(conn, "receipts", "pay_mode", "TEXT")
    _add_col(conn, "receipts", "pay_note", "TEXT")          # why: "chosen by <sales person> on the phone"
    _add_col(conn, "receipts", "pay_sure", "INTEGER")       # 1 = the sales person chose it
    _add_col(conn, "sales", "pay_mode", "TEXT")
    _add_col(conn, "sales", "pay_note", "TEXT")
    _add_col(conn, "delivery_drafts", "image", "TEXT")      # own copy of the photo (a draft that came in a setup file)
    # 1 = the owner never gave this item's cost: it is ASSUMED to be 65% of the selling price (sell 100 -> cost 65)
    # and follows the selling price when that changes. Typing a real cost in Supply sets it to 0.
    _add_col(conn, "items", "cost_auto", "INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE items SET cost_price=ROUND(sell_price*?, 2), cost_auto=1 WHERE COALESCE(cost_price,0) <= 0 AND sell_price > 0",
                 (COST_SHARE,))
    # how a payment came in: 'cash' or 'bank' (Payments page). NULL = a line from before: no account = cash, else bank
    _add_col(conn, "pay_entries", "method", "TEXT")
    # Round 15. What the receipt says next to each row: the price written on it, the description as read and the unit
    # (a row the shop does not carry can be added to Supply with these filled in)
    _add_col(conn, "receipt_lines", "price", "REAL")
    _add_col(conn, "receipt_lines", "item_text", "TEXT")
    _add_col(conn, "receipt_unmatched", "price", "REAL")
    _add_col(conn, "receipt_unmatched", "unit", "TEXT")
    # 1 = the stock of this item was not counted yet ("N/A, not sure yet" when it was added). It is sold without a stock
    # check, stays off the low-stock lists, and the dashboard Priority asks for the count until one is typed.
    _add_col(conn, "items", "qty_unknown", "INTEGER NOT NULL DEFAULT 0")
    # who delivers a trip: 'truck' (ours, with driver and helpers; NULL = truck), 'pickup' (the customer takes it with
    # his own vehicle) or 'other' (someone else: carrier = who, carrier_cost = what it costs)
    _add_col(conn, "bulk_order_runs", "mode", "TEXT")
    _add_col(conn, "bulk_order_runs", "carrier", "TEXT")
    _add_col(conn, "bulk_order_runs", "carrier_cost", "REAL")
    # 1 = the customer pays the delivery: the order's total = items + delivery. NULL / 0 = items only (orders before r15)
    _add_col(conn, "bulk_orders", "charge_delivery", "INTEGER")
    # Round 16. How much of this order row came out of Supply (items.quantity) when the order was saved: an order row
    # picked from Supply takes its quantity at once (editing it takes / gives back the difference, deleting the order
    # gives back what was not delivered). NULL = a row saved before r16, it took nothing yet (its next save takes it).
    _add_col(conn, "bulk_order_items", "stock_taken", "REAL")
    # Round 17. A Supply item deleted while it has history (sold, on receipts, bought): the row stays so past sales keep
    # its name ("Fine Sand (deleted)"), but it is gone from Supply, the phones, the receipt reader and every stock list.
    # NULL = a normal item. An item without any history is still really deleted.
    _add_col(conn, "items", "deleted_at", "TEXT")
    # Round 17. A row the sales person sent to the owner as a NEW Supply item (from the phone): the name, the price and
    # how many the customer bought. It waits under Supply (+ a dashboard card) until the owner accepts or removes it.
    # req_at NULL = an ordinary "not in Supply" row.
    _add_col(conn, "receipt_unmatched", "req_name", "TEXT")
    _add_col(conn, "receipt_unmatched", "req_price", "REAL")
    _add_col(conn, "receipt_unmatched", "req_qty", "REAL")
    _add_col(conn, "receipt_unmatched", "req_unit", "TEXT")
    _add_col(conn, "receipt_unmatched", "req_by", "INTEGER")
    _add_col(conn, "receipt_unmatched", "req_at", "TEXT")
    # Round 18. How many the sales person says are left in the shop (after this sale); the owner approves it on accept
    _add_col(conn, "receipt_unmatched", "req_stock", "REAL")
    # Round 18. A truck trip of its own in the Planner (no order): order_id NULL. Older databases had order_id NOT NULL:
    # the table is rebuilt once (same ids, same data) so it can hold such trips.
    runs_cols = conn.execute("PRAGMA table_info(bulk_order_runs)").fetchall()
    if any(c_[1] == "order_id" and c_[3] for c_ in runs_cols):
        _allow_trips_without_order(conn, [c_[1] for c_ in runs_cols])
    _add_col(conn, "bulk_order_runs", "purpose", "TEXT")    # NULL = delivers an order; deliver | get | personal
    _add_col(conn, "bulk_order_runs", "place", "TEXT")      # where the truck goes (a trip of its own)
    _add_col(conn, "bulk_order_runs", "note", "TEXT")       # what it carries / why
    _add_col(conn, "bulk_order_runs", "time", "TEXT")       # when it leaves ("08:00"), optional
    if conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 0:
        conn.executemany("INSERT OR IGNORE INTO units(name, to_kg) VALUES(?,?)", DEFAULT_UNITS)
    for t in conn.execute("SELECT id, capacity FROM transports WHERE capacity_value IS NULL AND capacity IS NOT NULL").fetchall():
        v, u = split_capacity(t[1])                                        # old free text like "4 mt" -> 4, metric tons
        conn.execute("UPDATE transports SET capacity_value=?, capacity_unit=? WHERE id=?", (v, u, t[0]))
    conn.commit()


def _allow_trips_without_order(conn, cols):
    """bulk_order_runs.order_id NOT NULL -> nullable: copy the table into a new one with the same columns and ids, then
    swap the names (SQLite cannot drop a NOT NULL). run_crew keeps pointing to the same run ids."""
    conn.commit()
    keep = [c for c in cols if c != "id" and c != "order_id"]
    extra = {"transport_id": "INTEGER REFERENCES transports(id)", "trips": "INTEGER NOT NULL DEFAULT 1", "date": "TEXT",
             "driver_id": "INTEGER REFERENCES employees(id)", "done": "INTEGER NOT NULL DEFAULT 0"}
    defs = ",\n    ".join(f"{c} {extra.get(c, 'TEXT' if c not in ('carrier_cost',) else 'REAL')}" for c in keep)
    names = ", ".join(["id", "order_id"] + keep)
    conn.executescript(f"""
BEGIN;
DROP TABLE IF EXISTS bulk_order_runs_r18;
CREATE TABLE bulk_order_runs_r18 (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER REFERENCES bulk_orders(id) ON DELETE CASCADE,
    {defs}
);
INSERT INTO bulk_order_runs_r18({names}) SELECT {names} FROM bulk_order_runs;
DROP TABLE bulk_order_runs;
ALTER TABLE bulk_order_runs_r18 RENAME TO bulk_order_runs;
COMMIT;
""")


def record_purchase(conn, item_id, qty, unit_cost, source="external", supplier=None, bought_at=None, note=None):
    """Add stock + log the purchase in ONE transaction. savings = (standard - paid) * qty."""
    from datetime import datetime
    when = bought_at or datetime.now().isoformat(timespec="seconds")
    with conn:
        it = conn.execute("SELECT cost_price FROM items WHERE id=?", (item_id,)).fetchone()
        if not it:
            raise ValueError("Unknown item")
        std = it[0]
        conn.execute("UPDATE items SET quantity = quantity + ? WHERE id=?", (qty, item_id))
        cur = conn.execute(
            "INSERT INTO stock_purchases(item_id,qty,unit_cost,standard_cost,savings,source,supplier,bought_at,note)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (item_id, qty, unit_cost, std, (std - unit_cost) * qty, source, supplier, when, note))
    return cur.lastrowid


DEFAULT_UNITS = [("kg", 1), ("g", 0.001), ("metric tons", 1000), ("sacks", None), ("bags", None), ("pcs", None),
                 ("boxes", None), ("rolls", None), ("sheets", None), ("lengths", None), ("meters", None), ("cu. meters", None),
                 ("liters", None), ("gallons", None), ("cans", None), ("trips", None), ("bottles", None), ("pairs", None),
                 ("packs", None)]

# No cost price given (the owner's list only has selling prices): the cost is taken as 65% of the selling price.
COST_SHARE = 0.65


def assumed_cost(sell):
    """the cost of an item whose real cost is not known: 65% of the selling price (100 -> 65)"""
    return round((sell or 0) * COST_SHARE, 2)


def cost_of(cost, sell):
    """the cost to count with: the real cost, or 65% of the selling price when it is 0 / missing"""
    return cost if (cost or 0) > 0 else assumed_cost(sell)
UNIT_ALIASES = {"mt": "metric tons", "ton": "metric tons", "tons": "metric tons", "metric ton": "metric tons",
                "t": "metric tons", "kgs": "kg", "kilo": "kg", "kilos": "kg", "sack": "sacks", "bag": "bags", "pc": "pcs",
                "piece": "pcs", "pieces": "pcs", "box": "boxes", "roll": "rolls", "sheet": "sheets", "length": "lengths",
                "meter": "meters", "m": "meters", "liter": "liters", "l": "liters", "gallon": "gallons", "can": "cans",
                "cu.m": "cu. meters", "cum": "cu. meters", "cubic meters": "cu. meters",
                "m.t.": "metric tons", "m.t": "metric tons", "tonne": "metric tons", "tonnes": "metric tons",
                "kilogram": "kg", "kilograms": "kg", "kg.": "kg", "pc.": "pcs", "pcs.": "pcs", "gal": "gallons", "trip": "trips",
                "bottle": "bottles", "bot": "bottles", "btl": "bottles", "pair": "pairs", "pack": "packs", "lit": "liters",
                "ltr": "liters", "litre": "liters", "litres": "liters", "liter.": "liters", "mtr": "meters", "mtrs": "meters"}


def unit_name(u):
    """'MT', 'Mt', 'ton' -> 'metric tons' (so typed units line up with the drop-down list)."""
    k = (u or "").strip().lower()
    return UNIT_ALIASES.get(k, (u or "").strip())


def split_capacity(text):
    import re as _re
    m = _re.match(r"\s*(\d+(?:\.\d+)?)\s*(.*)$", text or "")
    if not m:
        return None, None
    return float(m.group(1)), unit_name(m.group(2)) or "metric tons"


def stock_level(qty, a, b, c):
    """'A' critical, 'B' low, 'C' watch, None = fine. Out of stock is always 'A'."""
    q = qty or 0
    if a is not None and q <= a:
        return "A"
    if b is not None and q <= b:
        return "B"
    if c is not None and q <= c:
        return "C"
    return "A" if q <= 0 else None


def get_setting(conn, key):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def audit(conn, user_id, action, detail=""):
    """Write one line of the activity log (caller commits)."""
    from datetime import datetime
    conn.execute("INSERT INTO audit_log(at,user_id,action,detail) VALUES(?,?,?,?)",
                 (datetime.now().isoformat(timespec="seconds"), user_id, action, detail))


def receipt_label(created_iso):
    """Receipt file label: month + day + hour + '_' + minutes  ->  Jan 1, 1:30  =  '111_30'.
    (Not unique on its own: Jan 11 and Nov 1 at 1:30 both give '1111_30'. The exact time is still stored in receipts.created.)"""
    from datetime import datetime
    d = datetime.fromisoformat(created_iso)
    return f"{d.month}{d.day}{d.hour}_{d.minute:02d}"


if __name__ == "__main__":          # python shop_db.py  -> builds/migrates the schema and lists the tables
    import ocr_engine as E
    c = E.init_db()
    for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(n)
