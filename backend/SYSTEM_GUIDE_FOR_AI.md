# Shop Scanner: system guide (for a developer or another AI that must connect software to it)

Read this first. It describes the whole system as it works today: what each part does, the data contracts between them, and how to attach a desktop application safely.

## 1. What the system does

A shop worker photographs a paper receipt with a phone. The photo goes over the local network to a laptop, which reads it with PaddleOCR, matches each line to a product in the shop inventory, and returns the rows to the phone. The worker checks and fixes the rows on the phone, then presses **Update**: the laptop deducts the stock and records the sale for that worker.

```
 PHONE (Flutter app)                        LAPTOP (Windows, Python 3.11)
 ┌────────────────────┐   HTTP + JSON      ┌──────────────────────────────────────────────┐
 │ login, camera, crop│ ───────────────▶  │ server.py  (FastAPI, port 8000)              │
 │ outbox (pending    │   port 8000        │   accounts, sessions, REST API, receipts      │
 │ photos on phone)   │ ◀───────────────  │   OcrService: FIFO queue + OCR process       │
 └────────────────────┘                    │        │ multiprocessing queues              │
   holds NO business data                  │        ▼                                     │
                                           │ ocr_engine.py (PaddleOCR, parsing, matching, │
                                           │   stock check, commit_sale)                  │
                                           │        │                                     │
                                           │        ▼                                     │
                                           │ inventory.db (SQLite, one file, WAL mode)    │
                                           └──────────────────────────────────────────────┘
                                                  ▲
                    REACT DESKTOP APP (Tress/src, Vite) talks to the same server over HTTP:
                    owner-only routes under /api/shop/*  (supply, cash flow, sales monitor, bulk orders...)
```

**Folder layout (Tress project):**
```
Tress/
  backend/            <- everything Python lives here; run the server FROM here
    server.py  ocr_engine.py  shop_api.py  shop_db.py  requirements.txt
    inventory.db      (created on first start, EMPTY: no demo items)
    uploads/  uploads/thumbs/  avatars/      (created on first start)
  src/                <- React desktop app (pages/, lib/api.js = the only file that knows the server address)
```
The Flutter phone app is a separate project. It only needs the laptop address + port 8000; moving the backend folder changes nothing on the phone.

## 2. Files and who owns what

| File | Runs on | Role |
|---|---|---|
| `ocr_engine.py` | Laptop | **Processing library, no web and no window code.** Database helpers, OCR model loading, row grouping, fuzzy matching, stock check, `commit_sale`. Also runnable alone as a test (`python ocr_engine.py photo.jpg`, `--info`). |
| `server.py` | Laptop | **The API the phone talks to.** Imports `ocr_engine` as `E`. Owns accounts, sessions, the receipts tables, the OCR queue. CLI: `serve`, `add-user`, `list-users`, `passwd`, `set-avatar`, `disable-user`, `enable-user`, `cleanup-images`. |
| `inventory.db` | Laptop | **The only database.** Created next to `ocr_engine.py` (`DB_PATH = Path(__file__).with_name("inventory.db")`). |
| `uploads/`, `uploads/thumbs/`, `avatars/` | Laptop (next to `server.py`) | Photo files (`<receipt_id>.jpg`), thumbnails (`<id>_<width>.jpg`), profile pictures (`<username>.jpg`, 256x256). |
| Flutter app (`lib/*.dart`) | Phone | UI only: `main.dart`, `api.dart`, `outbox.dart`, `login_screen.dart`, `home_screen.dart`, `receipt_screen.dart`, `receipt_actions.dart`. |
| `shop_db.py` | Laptop | Every table that is not the OCR/receipt core (supply, people, cash flow, bulk orders, dashboard). `ensure_schema(conn)` is called by `E.init_db()`; it also runs the column migrations. |
| `shop_api.py` | Laptop | Owner-only routes `/api/shop/*` used by the React app. Plugged into `server.py` with `app.include_router(...)`. |
| React app (`Tress/src`) | Laptop | The owner's desktop window. Talks to `server.py` only through `src/lib/api.js`. Signs in with an owner account (`users.is_admin = 1`). |

Keep `server.py`, `ocr_engine.py` and `inventory.db` in the **same folder**. The database location is decided by where `ocr_engine.py` lives.

## 3. What the phone needs

**To get in (all typed or stored once):**

| Item | Where it comes from | Notes |
|---|---|---|
| Server address, e.g. `http://192.168.88.6:8000` | Printed by `python server.py serve`; typed in the app | Saved on the phone (SharedPreferences). Port defaults to 8000. Laptop should have a fixed IP. |
| Username + password | Created on the laptop: `python server.py add-user alice --name "Alice Reyes" --avatar alice.jpg` | The **Verify** button calls `POST /api/login`; the PC checks the account. |
| Token | Returned by login | Sent as `Authorization: Bearer <token>` on every call except `/api/ping` and `/api/login`. Valid 30 days. Saved in secure storage. A 401 anywhere sends the app back to the login page. |

**What the phone learns about the user after login (nothing else is needed):**

| Field | Endpoint | Used for |
|---|---|---|
| `id`, `username`, `display_name` | `POST /api/login` (`user` object) and `GET /api/me` | Name shown next to the profile circle. `id` is the key used for ownership of receipts and sales on the PC. |
| profile picture | `GET /api/me/avatar` (JPEG, 404 if none) | Circle image. Needs the Bearer header, so load the bytes with the header and show them from memory. |
| `active` | `GET /api/me`; the PC refuses login (403) or ends sessions (401) when an account is disabled | The green "Active" dot also reflects whether the PC answers `/api/ping`. |

**Data stored on the phone:** server address, token, and the **outbox** folder (`outbox/<client_id>.jpg`): photos that were taken but not yet accepted by the PC. The phone has **no copy of inventory, receipts or sales**. It always asks the PC. That is deliberate: one source of truth, nothing to synchronise.

**Offline behaviour:** every photo is saved to the outbox first, then uploaded. If the PC is unreachable the photo stays and is retried every 6 s while the app is open and when it returns to the foreground. The upload carries a `client_id` (the file name), so sending twice never creates a duplicate.

## 4. The database (`inventory.db`, SQLite, WAL mode)

```sql
items(id PK AUTOINCREMENT, name UNIQUE, unit, quantity REAL, sell_price REAL, cost_price REAL)   -- the stock
sales(id PK, created TEXT iso, image TEXT, total REAL, profit REAL, user_id INTEGER)             -- one row per Update; user_id NULL = desktop window
sale_items(id PK, sale_id, item_id, qty, sell_price, cost_price)                                 -- price snapshot at sale time
ocr_runs(id PK, created, device, model, max_side, img_w, img_h, seconds, lines)                  -- speed log
users(id PK, username UNIQUE NOCASE, display_name, password_hash, avatar, active, created)
sessions(token_hash PK, user_id, created, expires, last_seen)                                    -- SHA-256 of the token, never the token
-- users also has is_admin (1 = owner, may use /api/shop/* and the React app)
receipts(id PK AUTOINCREMENT, user_id, client_id, image, status, error, created, started, finished,
         seconds, device, model, UNIQUE(user_id, client_id))                                     -- one row per photo
receipt_lines(id PK, receipt_id, ocr_text, item_id, score, qty, verified, sold, sale_id)         -- rows read from a photo
```

Owner-side tables (`shop_db.py`, same file):
```sql
products(id, name UNIQUE, icon_key, created)                -- groups variants; items.product_id + items.spec added by migration
stock_purchases(id, item_id, qty, unit_cost, standard_cost, savings, source internal|external, supplier, bought_at, note)
      -- ONE running stock per item (no batches). standard_cost = items.cost_price at that moment.
      -- savings = (standard_cost - unit_cost) * qty  -> bought at 25 with a standard of 30 = +5 per unit extra profit
supply_sourcing(id, item_id, source internal|external, unit_cost, transfer_cost, maintenance_cost, UNIQUE(item_id, source))
positions(id, name)   employees(id, name, position, rate_per_hour, per_day_rate, present_days, absent_days, overtime_hours,
                                notes, photo, user_id UNIQUE -> users.id, active, created)
employee_cash_advances(id, employee_id, amount, note, date)   employee_payments(id, employee_id, kind payroll|bonus, amount, note, date)
obligations(id, kind loan|payment, name, party, start_date, end_date, freq_value, freq_unit, principal, interest_rate, final_payment, status)
obligation_payments(id, obligation_id, amount, note, date)
maintenance_items(id, name, amount, freq_value, freq_unit, start_date, last_paid, notes)   maintenance_payments(id, item_id, amount, date)
custom_types(id, label)   custom_items(id, custom_type_id, name, amount, basis per_employee|flat, notes)
attendance(id, employee_id, date, time_in, UNIQUE(employee_id, date))   cash_entries(id, date, direction in|out, amount, note)
transports(id, truck_name, plate, capacity, cost)   bulk_orders(id, customer, start_date, end_date, status active|done, completed_date)
bulk_order_items(id, order_id, name, qty, unit, price, delivered)   bulk_order_payments(id, order_id, amount, note, date)
bulk_order_runs(id, order_id, transport_id, trips, date)   priority_dismissed(key PK, dismissed_at)
settings(key PK, value)                 -- system_password = PBKDF2 hash; whoever knows it can make a desktop account
user_activity(user_id, date, first_seen, last_seen, PK(user_id, date))   -- written by current_user() on every request
audit_log(id, at, user_id, action, detail)                                -- what desktop users did (Calendar page)
-- items also has level_a, level_b, level_c (A-B-C stock alert levels, NULL = not used); products.image = product_images/<id>.jpg
```
**Receipt matching (ocr_engine.best_match):** an item is only considered when it shares a PRODUCT word with the receipt line
(`same_product`, word similarity >= KEYWORD_MIN 75; sizes/units like inch, kg, sacks are not product words). Lines that look like
order lines but match nothing are stored in `receipt_unmatched(receipt_id, ocr_text, item_text, qty)` and returned as `unmatched`
in `GET /api/receipts/{id}` (the phone lists them as "Not sold in this shop"); they are never sold. Quantities are read from
"<number> kg|sacks|bags|pcs|mt|..." (`QTY_UNIT_RE`).
**Commit options:** `POST /api/receipts/{id}/commit` body `{skip_short:true}` sells only what is fully in stock;
`{partial:true}` sells what the shop HAS of short items (asked 100 kg, have 50 kg -> sells 50 kg, the row's qty becomes 50),
response `reduced:[{item, unit, asked, sold}]`.
**Units:** `units(name, to_kg)`; `GET|POST /api/shop/units`. Trucks: `transports.capacity_value + capacity_unit` (old "4 mt"
text is migrated). Bulk order rows may carry `item_id` (picked from the inventory) and `kg_per_unit`; the React form suggests
return trips = ceil(order kg / sum of truck capacities). `PUT /api/shop/orders/{id}` edits an order (rows with `id` keep their
delivered tick). `POST /api/shop/orders/read-image` (multipart image) OCRs a picture through the same OCR queue
(`OcrService.read_now`, tags "tmp-...") and returns `{customer, rows:[{itemId,name,unit,price,qty}], unmatched}`; nothing is saved.
**Phone logins:** `PUT /api/shop/entries/emp-{n}/account {username, password?}` creates / renames / re-passwords an employee's
phone login, or links an existing unlinked login with that username. Sales counters: `GET /api/shop/sales-people?period=day|week|month|all`,
chart: `GET /api/shop/sales-series?group=day|week|month&count=N`.
**Desktop accounts:** `users.is_admin = 1`; for them `display_name = username` (the username is the name everywhere).
First run: `POST /api/setup` sets the system password and makes the first account. After that `POST /api/register
{system_password, username, password}` lets anyone who knows the system password make their own account, and the desktop
signs in with `POST /api/desk-login {system_password, username, password}`. Phone (sales) accounts stay `is_admin = 0`
and are made in Cash Flow -> Employee -> Phone login.
**Security (round 4):** `sessions.kind` = `desk` (setup / register / desk-login) or `phone` (`/api/login`). Every
`/api/shop/*` route, `/api/system-password` and `orders/read-image` need an admin user + a `desk` session + a request from
the laptop itself (`127.*`, `::1` or the laptop's own LAN address), else 401/403. `SHOP_ALLOW_REMOTE_DESK=1` or
`serve --allow-remote-desk` lifts the laptop-only rule. setup / register / desk-login are laptop-only too. Wrong passwords:
5 per 5 minutes per client address (all sign-in forms together) -> 429. Changing the system password deletes every other
`desk` session. **Packaged app:** `app_launcher.py` (PyInstaller, see BUILD_EXE.md) starts uvicorn in a thread and opens a
pywebview window at `http://127.0.0.1:8000/`; server.py serves the built React files (`dist/`, inside the exe as `web/`)
at `/` and any non-`/api` path (laptop only). When frozen, `ocr_engine.APP_DIR` (DB, uploads, avatars, product_images) is
the folder of the .exe. React `getBase()` uses `window.location.origin` unless it runs on the Vite port (517x/417x).
**Priority:** `GET /api/shop/priority` returns cards sorted by `score`: stock at level A 300-350, B 200-250, C 100-150
(deeper below the level = higher); loans / payments / maintenance due within 7 days `260 - 30 x days` (overdue 280+);
late bulk delivery 240; each card has `severity` and an `action` the dashboard uses to fix it in place.
**Phone discovery:** the server answers UDP `TRESNINOS_DISCOVER` on port 8765 with `{"app":"tres-ninos-shop","port":8000}`;
`/api/ping` also returns `app`. The Flutter app tries the last address, then the UDP broadcast, then scans x.x.x.1-254:8000.
Employee photo: uploaded in React (Cash Flow) to `avatars/emp<id>.jpg`. When the employee has a phone login (`employees.user_id`),
the photo is copied to `avatars/<username>.jpg` and `users.avatar` is set, so `GET /api/me/avatar` gives the phone the same picture.
Receipt label shown in React = month + day + hour + "_" + minutes of `receipts.created` (Jan 1, 1:30 -> `111_30`, 24-hour clock).

Relationships: `receipts.user_id → users.id`; `receipt_lines.receipt_id → receipts.id`; `receipt_lines.item_id → items.id`; `receipt_lines.sale_id → sales.id` (set when sold); `sales.user_id → users.id`; `sale_items.sale_id → sales.id`.

Rules that keep it consistent:
- `receipts.status` is `queued | processing | done | failed`. It describes the OCR job only. Whether rows were sold is derived from `receipt_lines.sold`.
- Stock is only changed by `commit_sale` (subtract) and by receipt deletion with undo (add back). Both run in one transaction.
- Passwords: PBKDF2-SHA256, 200,000 rounds, stored as `pbkdf2$<salt hex>$<hash hex>`.
- Old databases get `sales.user_id` automatically in `E.init_db()` (migration).
- Every connection should use `timeout=30` (the server and `E.init_db()` do) so a writer waits instead of failing with "database is locked".
- `server.db()` opens connections with `check_same_thread=False`: FastAPI may open a request's connection on one worker thread and run
  the endpoint on another. Without it, requests failed at random ("SQLite objects created in a thread can only be used in that same thread").

## 5. How `ocr_engine.py` works

**Public functions (the contract; do not change signatures without updating `server.py` and the desktop app):**

| Function | Purpose |
|---|---|
| `init_db() -> conn` | Creates tables, migrates `sales.user_id`, seeds 7 demo items if `items` is empty. Row factory is `sqlite3.Row`. |
| `get_items(conn)` | All items ordered by name. |
| `build_lines(boxes, conn) -> (lines, ignored_count)` | OCR boxes → rows. Each line: `{ocr, item_id, score, qty, verified}`. |
| `annotate_lines(conn, lines) -> [status]` | One status per line: `sold`, `no_qty`, `short` (not enough stock for all unsold rows of that item together), `ok` (score ≥ 85 or verified), `check`. |
| `stock_check(conn, lines) -> (items_by_id, need_by_item, short_item_ids, report_text)` | Aggregated demand vs stock. |
| `commit_sale(conn, lines, image_path, user_id=None, on_sale=None) -> (sale_id, total, profit)` | **One transaction:** insert sale, subtract stock (`UPDATE ... WHERE quantity >= need`), insert `sale_items`, set totals, then call `on_sale(cursor, sale_id)` inside the same transaction. Raises `ValueError` and changes nothing if stock ran out. `lines` = dicts with `item_id`, `qty`. |
| `log_run(...)`, `log(msg)`, `short_error(tb)` | Speed log, timestamped print, last line of a traceback. |
| `OcrWorker` | Starts the OCR **process** (spawn context) with two queues, see below. |
| `load_model(device, tier)`, `read_image(ocr, path, side)` | Direct use without the worker (what `python ocr_engine.py photo.jpg` does). |

**Reading a photo (inside the OCR process):** PaddleOCR (`PP-OCRv5` detection + English recognition, CPU or GPU) returns text boxes. The longest side is shrunk to `side` px (default 1280) before detection. `group_rows` merges boxes on the same horizontal line (within 0.6 × median box height) and sorts left to right. `parse_line` extracts `(item_text, qty)` from patterns such as `Nails 1/4 inch 2kg`, `Nails 1/4 inch: 2`, `2kg Nails 1/4 inch`, table rows with prices. `best_match` scores the text against every item name (rapidfuzz if installed, otherwise difflib): `norm()` unifies `1/4"`, `1/4in`, `l/4`; a different size number costs 35 points (`NUM_MISMATCH_PENALTY`) so `1/4` is not confused with `1/2`. Score ≥ 85 (`AUTO_OK`) is accepted as OK; below 60 (`MIN_SHOW`) the row is ignored unless it contains a `kg` quantity.

**Process protocol (this is how `server.py` talks to the engine):**

```
worker = E.OcrWorker();  worker.start()           # separate process, own memory
worker.jobs.put(("load", "cpu"|"gpu", tier))      # load a model            (tier: medium | v5mobile | small | tiny)
worker.jobs.put(("ocr", path, side, device, tier, tag))   # read one image; `tag` comes back (server uses the receipt id)
worker.jobs.put(None)                             # shut down
worker.out.get() ->  ("gpu_info",   {ok, name, ...})            first message after start
                     ("model_ready", {device, tier, note, seconds})
                     ("load_error",  {device, tier, msg})
                     ("ocr_done",    {boxes, seconds, w, h, side, device, tier, tag})
                     ("job_error",   {tag, msg})                # with a tag; without a tag the legacy ("error", traceback) is sent
```
`boxes` = `[{text, x, y, h}]`. The model loads in about 14 s on CPU, so jobs wait until `model_ready`.

## 6. How `server.py` works

**Life of one photo:**

1. `POST /api/receipts` (multipart `image`, `client_id`). Duplicate `client_id` for the same user returns the existing receipt. The image is validated, EXIF-rotated, shrunk to ≤ 2400 px, saved as `uploads/<id>.jpg`, a `receipts` row is inserted with `status='queued'`, and `OcrService.submit(id, path)` is called. The HTTP answer (202) returns immediately.
2. `OcrService` keeps a FIFO `pending` deque and an `inflight` dict. When the model is ready it sets `status='processing'` and puts an `ocr` job (with the receipt id as `tag`) on the worker queue.
3. A pump thread reads `worker.out`. On `ocr_done` it calls `E.build_lines`, replaces the rows in `receipt_lines` (all `verified=0`) and sets `status='done'`. If the receipt was deleted meanwhile, the result is dropped.
4. The phone polls `GET /api/receipts` (every 6 s on the list, every 3 s on an open receipt that is still queued/processing) and shows the rows from `GET /api/receipts/{id}`.
5. The worker edits rows (`PATCH` sets `score=100, verified=1`), then `POST .../commit` calls `E.stock_check` and `E.commit_sale(..., user_id=<worker>, on_sale=mark_sold)`, which marks those rows `sold=1, sale_id=<id>` in the same transaction as the stock deduction.

**Resilience:** on start-up, receipts left `queued`/`processing` (power cut, crash) are re-submitted. If the OCR process dies, unfinished jobs go back to the front of the queue and it is restarted on CPU; after 3 crashes the engine is marked broken (`/api/ping` shows `error`) and queued photos fail with a clear message (the app offers **Read again**).

**Security model:** login rate limit of 5 wrong passwords per 5 minutes per (client address, username); tokens are random, stored hashed; every receipt query filters by `user_id`, so a user can only see and change their own photos and sales. Plain HTTP on a trusted LAN only; never expose the port to the internet.

## 7. API reference

Base URL `http://<laptop-ip>:8000`. Errors are `{"detail": "text"}` unless stated. Interactive docs: `/docs`.

| Method + path | Body / query | Success response | Notable errors |
|---|---|---|---|
| `GET /api/ping` (no auth) | | `{ok, server, time, needs_setup, ready, device, model, queued, error}` | |
| `POST /api/setup` (no auth, laptop only) | `{system_password, username, password}` | `{token, user}` first run: sets the system password + first desktop account | 409 once set; 403 not the laptop |
| `POST /api/register` (no auth, laptop only) | `{system_password, username, password}` | `{token, user}` new desktop account | 401 wrong system password; 409 username taken; 429 |
| `POST /api/desk-login` (no auth, laptop only) | `{system_password, username, password}` | `{token, user}` desktop session | 401 wrong system password / account; 403 phone login or not the laptop; 429 |
| `POST /api/system-password` | `{old, new}` | `{ok}` (desktop session); other desktop sessions are signed out | 401 wrong old |
| `POST /api/login` (no auth) | `{username, password}` | `{token, user:{id, username, display_name}}` (a phone session) | 401 wrong; 403 disabled; 429 too many (per device) |
| `POST /api/logout` | | `{ok:true}` | |
| `GET /api/me` | | `{id, username, display_name, has_avatar, active}` | 401 |
| `GET /api/me/avatar` | | JPEG bytes | 404 none |
| `GET /api/items` | | `[{id, name, unit, quantity}]` | |
| `GET /api/stats?range=today\|week\|month` | | `{range, from, total, count}` (my sales only; week = last 7 days, month = last 30) | 422 bad range |
| `POST /api/receipts` | multipart `image`, `client_id` | 202 `{id, status:"queued", duplicate}` | 413 > 15 MB; 422 not an image |
| `GET /api/receipts?limit=40` | | `[{id, status, state, error, created, n_lines, n_sold, seconds, image_url}]` newest first. `state` = queued, processing, failed, ready, partial, sold | |
| `GET /api/receipts/{id}` | | summary + `order_total` + `lines:[{id, ocr_text, item_id, item_name, unit, qty, score, stock, status, line_total, sold}]`; line `status` = ok, check, short, no_qty, sold | 404 |
| `GET /api/receipts/{id}/image?w=400` | | JPEG (optional thumbnail, 64 to 1600 px) | 404 |
| `POST /api/receipts/{id}/retry` | | `{id, status:"queued"}` | 409 if not failed |
| `PATCH /api/receipts/{id}/lines/{lid}` | `{item_id?, qty?}` | whole receipt (as above) | 409 already sold; 422 qty ≤ 0 or unknown item |
| `POST /api/receipts/{id}/lines` | `{item_id, qty}` | whole receipt | 422 |
| `DELETE /api/receipts/{id}/lines/{lid}` | | whole receipt | 409 already sold |
| `POST /api/receipts/{id}/commit` | optional `{skip_short:true}` | `{sale_id, total, sold, skipped:[names]}` | 409 still being read / nothing left; 422 a row has no quantity; **409** `{error:"not_enough_stock", short:[{item_id,item,unit,need,have,missing}], message}`; 409 `{error:"nothing_sellable"}` |
| `DELETE /api/receipts/{id}` | optional `?revert=true` | `{deleted, reverted_sales, restored_total}`. Removes rows and picture files | **409** `{error:"has_sales", sold_rows, total, message}` unless `revert=true`; with it, the sales are deleted and the stock is added back |

Owner routes for the React app (all need an owner token; `<img>` URLs may use `?token=`): `GET /api/shop/supply`,
`POST /api/shop/supply/products`, `POST .../products/{id}/types`, `PATCH|DELETE /api/shop/supply/items/{id}`, `POST .../items/{id}/adjust`,
`POST .../items/{id}/purchase`, `GET|PUT .../items/{id}/sourcing`; `GET /api/shop/cashflow`, `POST /api/shop/entries`, `PUT|DELETE /api/shop/entries/{id}`,
`POST /api/shop/entries/{id}/log/{cashAdvances|customPayments|payments}`, `POST .../entries/{id}/photo`, `POST .../entries/{id}/account`;
`GET /api/shop/sales-people`, `POST .../sales-people/{uid}/remove`, `GET .../sales-people/{uid}/receipts|transactions`, `GET /api/shop/receipts/{id}[/image]`;
`GET|POST /api/shop/orders`, `POST /api/shop/orders/{id}/items/{iid}/toggle|payments|complete`, `DELETE /api/shop/orders/{id}`, `POST /api/shop/transports`;
`GET /api/shop/stats|sales-daily|sales-monthly|cash|attendance|priority`, `POST /api/shop/cash|attendance|priority/dismiss`;
`POST|GET|DELETE /api/shop/supply/products/{id}/image`; `GET /api/shop/calendar?start&end`, `GET /api/shop/calendar/day/{date}`,
`GET /api/shop/calendar/day/{date}/download` (ZIP), `GET /api/shop/storage`, `GET /api/shop/archive?before=` (ZIP),
`POST /api/shop/archive/cleanup {before}` (deletes finished receipt photos only). Full list: `/docs`.

Phone-side status words: list tiles show Waiting (queued), Reading… (processing), Failed, Check it (ready), Partly sold, Sold.

## 8. Connecting the desktop app

Choose one pattern. **A is recommended.**

**A. Shared database, two programs side by side (no code changes needed).**
The desktop window keeps importing `ocr_engine` exactly as before; the phone server runs as its own program (`python server.py serve`) in the **same folder**. Both open the same `inventory.db`:
- Desktop sales: `E.commit_sale(conn, lines, image_path)` still works (`user_id` stays `NULL`).
- Stock changes made by either side are seen by the other immediately (WAL mode; always `sqlite3.connect(DB_PATH, timeout=30)`; `E.init_db()` already does this).
- Show phone sales on the desktop:
  ```sql
  SELECT s.id, s.created, u.display_name, s.total, s.profit
  FROM sales s LEFT JOIN users u ON u.id = s.user_id        -- NULL user = sold on the desktop
  ORDER BY s.id DESC;
  ```
- Show photos waiting for a person: `SELECT * FROM receipts WHERE status='done' AND EXISTS (SELECT 1 FROM receipt_lines WHERE receipt_id=receipts.id AND sold=0)`.
- Cost: each program loads its own copy of the OCR model (extra memory, and about 14 s to start on CPU). If the desktop window does not need its own OCR any more, use pattern C.

**B. One process.** Start the server inside the desktop app (`uvicorn` in a thread) and reuse one OCR process. This needs `OcrService` to share the desktop's `OcrWorker` and result handling; it is possible but has to be written, and a crash then takes both down. Prefer A or C.

**C. Desktop as a client of the same API.** The desktop app calls `http://localhost:8000` (login once as a normal user, then use the endpoints in section 7). One OCR model, one code path, the desktop and phone behave identically. Requires an account for the desktop (`add-user desktop`).

**Rules for any code that touches the data:**
1. Never write stock by hand. Use `E.commit_sale` (or the API) so stock, sale and sale rows change together.
2. Do not rename tables or columns, or the message names in section 5, without changing both `server.py` and `ocr_engine.py`.
3. Keep a single `inventory.db`. If the desktop app lives in another folder and imports a *copy* of `ocr_engine.py`, it will create a *different* database. Import the one file or set `DB_PATH` explicitly.
4. `ocr_engine.OcrWorker` uses `spawn`: any script that starts it must be guarded by `if __name__ == "__main__":` (Windows).
5. Receipt ids are `AUTOINCREMENT` and never reused; photo files are named after them.

## 9. How to add something (checklist for an AI)

1. **New data field:** add the column in `E.init_db()` *with a migration* (`PRAGMA table_info` check + `ALTER TABLE`), because the owner's database already exists.
2. **New endpoint:** add it to `server.py` with `user=Depends(current_user), conn=Depends(get_db)`, filter by `user["id"]`, and use `with conn:` for multi-statement writes. Document it in section 7.
3. **New phone call:** add a method to `api.dart` (it already adds the token, the timeout and the offline error), then use it from a screen. Show `ApiException.message` to the user.
4. **Changing OCR/matching:** edit only `ocr_engine.py` (`parse_line`, `norm`, `similarity`, constants at the top). Test without the app: `python ocr_engine.py photo.jpg`.
5. **After any change:** restart `python server.py serve`. Check `GET /api/ping` shows `ready: true`.

## 10. Operating notes

- Start: `cd backend` then `python server.py serve` (add `--device cpu|gpu`, `--tier`, `--side`, `--port`). The first line printed lists the address to type in the app.
- First run: open the React app; it offers "Create the owner account" while none exists (or `python server.py add-user owner --admin`).
- Sample items for an OCR test: `python server.py seed-demo` (the database otherwise starts empty).
- Free disk space: delete a receipt in the app (long-press in the list, or the bin icon on the receipt page). Old finished receipts: `python server.py cleanup-images --days 30 [--dry-run]` removes the photo files only; sales records stay.
- Network setup, firewall and security notes: `CONNECTION_PROCESS.md`.
- The NumPy 1.x/2.x warning printed by `torch` at OCR start-up is harmless while the model loads ("model ready").

## Round 6
**Progression right column = Cash In vs Cash Out** (`src/components/cashflowGraph.jsx`): `GET /api/shop/cashflow-breakdown
?period=month|3m|12m|all` -> `{in:[slice], out:[slice], totalIn, totalOut, net, series:[{key, in, out}], byDay}`; slice =
`{key, label, kind, amount, count, items:[{date, label, note?, amount}]}`. In: sales per person (one item per day),
bulk order payments, manual cash in. Out: payroll, bonuses, cash advances, one slice per obligation, one per maintenance
item, stock purchases, manual cash out. (The round-5 chat advisor and AI download were removed.)
Cash Flow items count what is DUE in the period (window = period start .. end of the current month): obligations by their
schedule (start_date + k x frequency, `final_payment / periods` each, the first N = number of logged payments are "paid"),
maintenance from start_date (dates <= last_paid are paid), salaries = per_day_rate x attendance days in the period (or
present_days when that person has no attendance rows), custom items (SSS...) once a month. Each slice: `due`, `unpaid`,
items with `status` paid / to pay / overdue; response `outPaid`, `outUnpaid`. If more was paid than due, payments count.
**Data folder of the .exe:** `ocr_engine.APP_DIR` = `%LOCALAPPDATA%\TresNinosShop` when frozen (`SHOP_DATA_DIR` overrides;
backend/ with plain Python). `ocr_engine.move_old_data()` (called by app_launcher) moves data that old versions kept next
to the .exe and rewrites `receipts.image` paths; if both places have a database the old one goes to
`from_program_folder_<time>/`. shop_server.log lives there too.
**Profile:** `GET /api/shop/me`, `POST|DELETE /api/shop/me/photo` (avatars/user<id>.jpg); `GET /api/desk-avatar/{username}`
(no auth, laptop only) for the sign-in screen; calendar day `inCharge[].uid` for avatars.
**Launcher:** single instance (Windows named mutex; a second start focuses the window), refuses to start next to a
`python server.py` already on port 8000, restarts the server thread if it dies; uvicorn logs go to shop_server.log.

## Round 7
**Phones on a new Wi-Fi:** Windows marks a new network Public, and the firewall rule for the shop is Private-only, so
phones cannot reach the laptop. `GET /api/shop/network` (desk) -> `{addresses, port, networks:[{name, alias, category,
connectivity}], public:[names]}` via PowerShell `Get-NetConnectionProfile` (cached 20 s; Windows only).
`POST /api/shop/network/fix` writes `fix_network.ps1` to the data folder and runs it elevated (UAC): connected Public
profiles -> Private, firewall rules "Tres Ninos Shop (app)" (program), "(port 8000)" TCP, "(phone finder)" UDP 8765,
all Profile Private. The desktop shows a banner + a "Phones connect to this laptop at" box in the profile window.
**Concurrency (tested: 6 phones x 7 uploads at once, 42 simultaneous Updates on 100 sacks):** uploads with the same
client_id at the same moment return the existing receipt (IntegrityError caught); `commit` marks rows sold with
`AND sold=0` inside the stock transaction and rolls everything back with 409 `already_sold` on a double tap; stock is
deducted with `quantity >= need` so it never goes below zero. OCR stays one photo at a time (FIFO).
**Phone app:** launcher icons (Android mipmaps, iOS AppIcon) = the shop logo; app name "Tres Niños".

## Round 8
**Dashboard:** `/api/shop/stats` lowStock rows carry `id, costPrice, sellPrice, levelA..C` (sorted A, B, C, then qty);
the Low Stock card opens a list where each item can be restocked / re-levelled / recounted in place (`ItemFix` in
main.jsx, same as the priority card). `GET /api/shop/sales-summary?period=day|week|month|all` -> `{people:[{userId, name,
total, count}], counters, desktop:{total,count}, orders:[{id, customer, date, total, paid, status}], ordersTotal,
ordersPaid, total}`; total = counters + desktop + orders created in the period (value = SUM(qty*price)). Used by the
Today's Sales card (clickable) and the Sales page total bar.
**Supply:** the count is a typed number + Apply (POST adjust with `delta = new - old`); bought stock still goes through
Edit -> Buy stock (`/supply/items/{id}/purchase`).
**Phone avatar:** `server.avatar_file(conn, user)` = users.avatar, else the photo of the employee linked by user_id, else
an employee with the same name; used by `/api/me` (has_avatar) and `/api/me/avatar`.
**Monthly share:** in cashflow-breakdown a maintenance item or obligation due less often than monthly
(`is_long`: cycle > 31.5 days) is not counted in full on its due month: every month of the window gets
`monthly_share` = amount / freq_value (months) or amount x 30.44 / days, for the next due date after that month
(paid once that due date is paid). Slice gets `shared, every, full`. Progression -> Recurring Payments donut is per month too.
**To save:** `GET /api/shop/savings-plan` -> `recurring:[{kind, id, name, amount, every, months, monthly, nextDue,
monthIndex, shouldHave, status saving|overdue|no date, monthsLeft}]` (only costs due every 2+ months; shouldHave =
monthly x months into the current cycle, capped at amount), `restock:[{id, name, unit, units, since, sinceKind
bought|month, saved, rebuy, extra, profit, costNow, qty, level}]` = per item, units sold since its last stock purchase
(no purchase logged: since the 1st of the month); saved = SUM(qty x sale_items.cost_price at the sale), rebuy = units x
items.cost_price now, extra = rebuy - saved (a cost increase is paid from the profit), plus totals, `cogsThisMonth`,
`saveThisMonth` = recurringMonthly + cogsThisMonth. Shown at the bottom of Cash In vs Cash Out ("TO SAVE").
**Payments page** (`src/pages/payments.jsx`, nav below Calendar). Tables `pay_accounts(bank, nickname, holder, last4,
opening, opening_date)` and `pay_entries(account_id NULL=cash, direction in|out, amount, date, party, ref, note, order_id,
order_payment_id, photo)`. NO card/account numbers, CVV, PIN or bank passwords: last4 max 4 digits, account text with
8+ digit numbers and entry text with 15+ digit numbers are refused (422). Nothing connects to a bank.
`GET /payments` -> `{banks:[{bank, balance, accounts:[{id, bank, nickname, holder, last4, opening, in, out, count,
balance}]}], cash:{count,total}, orders:[{id, customer, date, orderStatus, total, paid, balance, state
complete|incomplete|missing, delivered, payments:[{id, entryId, date, amount, note, where, ref, hasPhoto}]}], owed}`
(BDO, GCash, Landbank, ChinaBank always listed, other bank names after). `POST|PUT|DELETE /payments/accounts[/{id}]`.
`GET /payments/passbook?account=<id>` (no account = cash) -> lines oldest first with running `balance`; entries of one
order are ONE line `{kind:'order', orderId, party, entries:[...], amount, state, orderTotal, orderPaid}`.
`POST /payments/entries` with orderId also inserts the `bulk_order_payments` row (so Sales sees it; the Sales page is
unchanged) and stores its id; `DELETE /payments/entries/{id}` removes both. `POST /payments/link?paymentId=&accountId=`
puts a payment typed in Sales into an account (or cash). Receipt photos: `POST|GET /payments/entries/{id}/photo`
(payment_photos/<id>.jpg). Deleting an order keeps its bank lines (link cleared).
**Bank / PayMongo feasibility (Oct 2026):** BDO, Landbank, ChinaBank offer no API for a small shop to read its own
account; BSP Open Finance (Circular 1122) is still being rolled out by the banks. PayMongo (QR Ph / GCash) has an API and
webhooks but needs a verified PayMongo business account, a secret key and internet, and webhooks need a public HTTPS
address (the shop laptop is on local Wi-Fi). So the Payments page is a manual passbook; a PayMongo pull (list payments
with the secret key) could be added later if the shop opens a PayMongo account.

## Round 9
**Staff (Dashboard card, `src/components/staffPanel.jsx`):** `employees.temporary`, `employees.undertime_hours` (new
columns) and table `work_hours(employee_id, date, ot_hours, ut_hours, note, UNIQUE(employee_id,date))`.
`GET /attendance?date=` rows also carry `temporary, perDayRate, ratePerHour, ot, ut`. `mark_present()` /
`set_hours()` keep `present_days`, `overtime_hours`, `undertime_hours` (the Cash Flow cycle totals) in step by the
difference. `POST /employees/temporary {name, role, perDayRate, ratePerHour, presentToday}` (same name + temporary =
re-activated), `POST /employees/{id}/end-temporary` (active=0, days stay), `POST /work-hours {date, rows:[{employeeId,
ot?, ut?}]}` (0-24 h), `GET /payslip?start&end` -> per person `days:[{date, present, timeIn, ot, ut, pay}]`, gross =
days x per_day_rate, otPay = ot x rate_per_hour x 1.25, utLess = ut x rate_per_hour, + bonuses - cash advances = net,
balance = net - payroll paid; `PUT /payslip/day {employeeId, date, present, timeIn HH:MM, ot, ut}`. Cash Flow / Progression
pay formulas subtract undertime x hourly rate.
**Saved data viewer (Calendar):** `POST /archive/open` (form field `file`: archive ZIP -> its inventory_backup.db,
day ZIP -> parsed CSV JSON, or a raw .db; validated, stored in `opened_data/<id>.db|json` + index.json),
`GET /archive/opened`, `DELETE /archive/opened/{id}`, `GET /archive/opened/{id}/days` (date, sales, nSales, present,
orders), `GET /archive/opened/{id}/day/{date}` -> `{present:[{name, position, timeIn, ot, ut}], salesByPerson:[{person,
count, total, items:[{item, unit, qty, amount}]}], orders:[{customer, items, total, paidToday, paidSoFar, status,
events}]}`. Saved databases are opened read-only (`mode=ro`), missing tables of old versions are skipped.
**Transfer (`src/pages/transfer.jsx`):** `GET /transfer/export` (admin_q) -> TresNinos_setup_<date>.zip = setup.json
`{app:'tres-ninos-shop', kind:'setup', version:1, made, from (hostname), by, counts, sections:{employees:{positions,
rows}, obligations, maintenance, benefits, supply:{units, products:[{name, photo, items:[... sourcing]}]}, trucks,
orders, banks (+ a bank:null row = cash payments), cash}}` + photos/. `POST /transfer/read` (file) stores it in
transfer_in/<token>.zip and returns per section new / changed (with field names) / same; `POST /transfer/apply {token,
sections, updateExisting, stockCounts}`. Match keys: employee name, obligation name+kind, maintenance name, benefit
type+name, item name, truck name+plate, order customer+created, bank bank+nickname+last4. Logs are added only when the
same (date, amount, ...) line is missing, so a second apply adds nothing. Never moved: sales, receipts, users,
passwords, settings.

## Round 10
**Bills with no end date** (`obl_periods`): a `payment` obligation without end_date keeps coming back (each = full
final_payment, schedule never stops); a loan without end date is one payment. So "Tire change P2,300 every 6 months"
counts P383.33 in every month (3 months = P1,150). Savings plan fixed: the first due date is the start date (k=0).
**General attendance:** `GET /attendance/range?start&end` (max 62 days) -> `{days, staff, present:{empId:[dates]}}`;
`POST /attendance/bulk {marks:[{employeeId, date, present}]}` (only the changed boxes; a day checked here has time_in ''
and "checked" means a row exists). Dashboard tab "General" (staffPanel.jsx): grid, Apply to all, skip Sundays, click a
day / a name to toggle. Temporary tab: job title drop-down (positions) + "Type another job".
**Payments:** `/payments` lists only banks that have an account, plus `suggest` names for "Add a bank".
`GET /payments/day?date=` -> `{orders:[{customer, payments:[{amount, method, kind bank|cash|unknown, time, ref}],
orderTotal, orderPaid, state}], people:[{person, sales:[{time, total, items, method:'Cash'}], total}], other, byMethod,
total}` (phone sales have no payment mode yet = cash). Completed / incomplete orders show method chips.
**Transfer + sales:** `GET /transfer/export?sales=1[&salesFrom&salesTo]` adds `sections.sales:[{created, total, profit,
person:{username,name}|null, items:[{item, unit, qty, sell_price, cost_price}]}]`; employees carry `login:{username,
name}`. Apply: sales matched by (created, total, username), sales persons = same username or a new phone login with an
unusable password ("!moved-no-password": set it in Cash Flow), missing items made with stock 0, stock is NOT changed.
Plan returns `newCount/changedCount` (lists cut at 300) and a per-person `summary` for sales.
**Mock data:** `mock_data/TresNinos_MOCK_30days.zip` (Sep 23 - Oct 22 2026): 9 employees (2 cashiers, 3 sales persons
with phone logins paolo/liza/ramon, driver, 2 labor, backhoe operator) + 2 temporary helpers, attendance with absences,
overtime/undertime, weekly payroll, cash advances, a bonus, 211 sales, 15 items, loan, bills (tire change every 6
months, electric bill), maintenance, SSS, a truck, 4 orders paid by BDO / GCash / cash, BDO + GCash accounts.

## Round 11
**Delivery crew:** `bulk_order_runs.driver_id`, `.done` and table `run_crew(run_id, employee_id)` (helpers);
`bulk_orders.address`. RunIn now has `id` (editing keeps the run, its crew and its done tick), `driverId`, `helperIds`
(None = keep), `done`. `GET /crew?date=&run=` -> `{drivers, helpers, isPast, isToday}`: drivers = job matches "driver",
helpers = job matches labor/helper/pahinante/porter/loader/utility/crew/kargador + TEMPORARY workers (on a past day only
if checked in that day; today/later: every temporary still on the list); each with `present` and `busy` (other runs that
day). `PUT /runs/{id}` (only the fields sent: date, transportId, trips, driverId, helperIds, done). Deleting an employee
takes them off every run; deleting an order removes its crew. Transfer carries the crew by name.
**Planner page** (`src/pages/planner.jsx`, nav below Transfer): `GET /planner?start&end` -> `{days:[{date, runs, done,
noDriver, noHelper, overdue, crew, doubleBooked}], unscheduled (runs without a day), ordersWithoutRun, upcoming,
overdue}`; `GET /planner/day/{date}` -> runs (customer, address, items, truck, driver, helpers, status
overdue|today|upcoming|done|unscheduled) + pool + stats (driversFree, helpersFree, noDriver, noHelper, doubleBooked) +
freeHelpers/freeDrivers. Quick "add temporary help" uses /employees/temporary. Shared picker: `src/components/crew.jsx`.
**Delivery receipts from the phones:** after the OCR reads a phone receipt (`OcrService._store_result`), shop_api's
module-level `save_delivery_draft()` checks the text rows for DELIVERY_RE ("delivery", "deliver to", "D.R. No.", "ship
to", "drop-off"); if found it stores `delivery_drafts(receipt_id UNIQUE, user_id, status new|done|dismissed, customer,
address, delivery_date, data JSON {rows, unmatched, words, text}, order_id)`. Customer / address / date come from
`text_customer`, `ADDRESS_RE`, `text_date` (also used by Add Order -> Read from photo). `GET /delivery-drafts?status=&userId=`,
`GET /delivery-drafts/{id}`, `POST .../dismiss`, `POST .../apply` (an OrderIn; creates the order in the same transaction
and marks the draft done; a second apply = 409). `/sales-people` rows have `drafts` (green bubble on the counter);
`/priority` puts a `kind: "delivery"` card (score 420) on top. Frontend: `DraftOrderModal` exported from sales.jsx
(the Add Order form filled in + receipt thumbnail), used by the Sales counter bubble and the dashboard card.
**OCR quantity:** `parse_line` rule 0: a row ending with unit price + line total takes as quantity the number that makes
price x qty = total ("Cement 40kg 20 sacks 285.00 5700.00" -> 20, not 40).
**Payments by day:** each sale has `lines` (item, unit, qty, price, amount); rows expand to show them.
**Calendar day:** `deliveries` (customer, truck, driver, helpers, done) + deliveries.csv in the day ZIP.
**Delete all records:** `POST /danger/delete-all {systemPassword, username, password, keepPhoneLogins}`. server.py's
`reauth()` checks the system password and that username = the signed-in desktop account (403 when wrong, never 401,
which would sign the person out; counts toward the 5-in-5-minutes lock). It first copies the database (sqlite backup)
and moves uploads/, product_images/, payment_photos/, opened_data/, transfer_in/ and employee photos into
`backups/before_delete_all_<time>/`, then empties every table except users, sessions, settings (phone logins removed
unless kept), restores the default units, logs it, VACUUM. Also: a wrong current password in "Change system password"
now answers 403 (it used to sign the person out) and counts toward the lock.
**Checks:** every GET route answers without a server error on the mock data, on an empty database and on an older
database (migrations); Calendar, Payments by day, sales counters, sales summary and Progression give the same totals.

## Round 13
**Version check (an old server still running):** `server.py` `SERVER_VERSION` (also in `/api/ping` as `version`) and
`src/lib/api.js` `SERVER_VERSION` must be the same string: bump BOTH with every update. In dev (`npm run dev`) the app
shell shows a red "Restart the shop server" banner when they differ, and a 405 / bare "Not Found" from `/api/*` becomes
`RESTART_MSG` (a POST to a route the old server does not have falls into the GET-only SPA catch-all -> 405). The .exe
serves its own pages, so it never shows this.
**Removing a person = settle, then hide (not delete):** `employees.removed` (date). `GET /employees/{n}/settlement` ->
`payslip_for` over ALL their records + `owed` (balance > 0) / `theyOwe` (advances > pay) / `from`/`to` / `lastPay` /
`login` / `futureRuns` (runs not done, dated today or later or no day, where they drive or help).
`POST /employees/{n}/remove {pay, note}`: pay > 0 -> payroll row "Final pay - note"; active=0, removed=today; a non-admin
phone login is switched off (users.active=0, sessions deleted; the employee link stays); taken off `futureRuns` (past
runs keep them). `POST /employees/{n}/restore` (login on again) -> the Cash Flow entry. `GET /employees/removed` (with
owed). Old `/employees/{n}/end-temporary` still works (sets removed too). `payslip_for`: `net` is rounded BEFORE
`balance = round(net - paid, 2)`, so paying the shown balance leaves 0. `GET /payslip?...&hidden=1` (default hides removed
people; returns `hiddenCount`, `hiddenTotal`, `hiddenOwed`); `GET /attendance/range?...&hidden=1` (`hiddenCount`).
Counters: `GET /sales-people/removed`, `POST /sales-people/{uid}/restore` (relinks a same-named employee). Frontend:
`src/components/settle.jsx` (SettleModal: Temporary -> Done, Cash Flow -> Remove…, "owed" tags), `kit.jsx`
`HiddenToggle` ("Show hidden (n)") and `PasswordInput` (eye button; used by Delete all, system password, phone logins).
**One bank for every bank / e-wallet sale (NetBank):** no preset banks any more ("Add a bank" = a typed name). The "sales
bank" = setting `sales_bank_account`, else the first account; `POST /payments/accounts/{id}/sales-bank`.
`sales.pay_mode` 'cash'|'bank' (NULL = before this = cash) + `pay_note` (why). `account_dict` adds bank sales (since its
`opening_date`) to `in`/`balance` of the sales bank (`salesIn`, `salesCount`, `isSalesBank`); overview has `salesBank`,
`bankSales`; passbook of the sales bank has `kind:"sales"` lines (one per day per sales person, `sales:[{id,time,total,why}]`).
`/payments/day`: each sale `payMode`, `method` (= the bank's label "NetBank · nickname", same as its order payments, or
"Cash"), `why`; `byMethod` per method. `PUT /sales/{id}/payment {mode}` switches one sale. Cash on hand (`/cash`) is
unchanged: it counts all money the shop received.
**Cash or bank read from the photo (REMOVED in round 17 — the phone asks, see Round 17):** `ocr_engine.detect_payment(boxes, image)` -> `{mode, sure, why}`, called in
`_store_result` (stored in `receipts.pay_mode/pay_note/pay_sure`; commit copies it to the sale). Order: CASH SALES box
ticked -> cash; a bank / e-wallet word (bank, NetBank, GCash, Maya, InstaPay, PESONet, online, QR Ph, BDO, BPI...) -> bank;
CASH SALES printed but not ticked (or CHARGE SALES ticked) -> bank; "paid in cash" / COD / "terms: cash" -> cash; nothing
-> cash, sure=False. `tick_marks()`: around each OCR box holding "cash sales" / "charge sales" (the reader often glues
them: "CASH SALESCHARGE SALES"; each "...SALES" is one slot), straighten by the box angle, adaptive threshold, connected
components; tick boxes = about square and taller than the letters; ink share inside the box (inner 56%): >= 0.12 ticked,
< 0.05 empty. Tested on the real crop: ticked 0.38-0.53, empty 0.0 (also turned 5-6°).
**OCR geometry + handwritten delivery receipts:** `read_image` boxes now also have `w`, `cx`, `a` (text angle, degrees)
and `th` (letter height along the tilt) (`box_info`). `group_rows` -> `straighten()`: a tilted photo (median angle of
wide boxes >= 0.8°) gets each box's y moved along the LOCAL tilt (wide boxes within 4 line heights), so the qty column
joins its article row and "Address:" gets the handwriting written on its line. Boxes without angles (old results,
tests) are grouped exactly as before. `parse_line` rule 0b: `lead_qty` (first column: "10 pc", "to p" = 10, "3.PL") and
`price_total_qty` ("(815) = 24,450" -> 30; tries the endings of a glued digit run "2x315815/=24450"; a 1-2 digit ending
only when it agrees with the lead qty). `build_lines_full(boxes, conn, loose=True)`: rows inside the article table
(after ARTICLES / PARTICULARS / DESCRIPTION, before TOTAL / RECEIVED / ...) match on a product word >= 65 (a 2-letter
scrap only >= 80), score "Please check", ranked by keyword + token_set similarity; every unmatched table row is listed.
`_store_result` uses the loose rows only for the delivery draft (the phone's own receipt rows are unchanged); Add Order ->
Read from photo uses loose. `read_delivery`: printer lines (Date Issued, accreditation, BIR, Authority to Print) never
give the date; a table header after "Address:" is not the address; `text_customer` also stops at "Terms:" / "TIN:".
Real check (PaddleOCR PP-OCRv5_server_det + en_PP-OCRv5_mobile_rec on the owner's DELIVERY photo): all 8 articles go to
the right Supply item; quantities: 5 right, 1 not read, 2 read "2" for 20 (handwriting).
**Read again:** `POST /receipts/{rid}/read-again` (desktop; `build_router(..., reread=)` -> `SERVICE.submit`): refused
when part is sold (409) or it is being read; the draft refreshes when the reader is done. `draft_dict` adds
`receiptStatus`, `canReadAgain`, `payment`; `/receipts/{rid}` (desktop) adds `status`, `payment`.
**Transfer:** new section `drafts` (pending delivery orders: the receipt with its photo `photos/receipts/draftN.jpg`,
unsold rows and unmatched rows; items found by name; same draft = same created + username). Sales carry `pay_mode`,
`pay_note`; equal sales (same second, total, person) are COUNTED (`sales_here`), not merged; a kept sale takes the file's
pay mode with "update existing" (plan: "changed: paid by bank"). Banks carry `sales_bank`. Orders: delivery runs are now
synced for EXISTING orders too (`runs_here`: same day + truck = same run; missing runs added; with update existing the
crew and the delivered tick follow the file); the plan says "N delivery run(s)" / "crew / delivered of N run(s)".
**Saved data viewer / day ZIP:** `archive/opened/{vid}/day/{day}` has `deliveries` (DB copies: runs + driver + run_crew,
older databases without those columns are fine; day ZIPs: deliveries.csv); `/days` has `deliveries` per day; the day
ZIP's summary.txt lists DELIVERIES. Opened .db copies are switched to journal_mode=DELETE and "remove" also deletes
-wal / -shm / -journal.
**Mock data:** `make_mock.py <data dir> <out.zip> <DELIVERY photo> <its OCR boxes .json>`: the same 211 sales (now cash /
bank with the reason), ONE bank (NetBank, the sales bank), 9 orders with 18 truck runs (done, late, today, the next two
weeks, no day yet), the receipt items in Supply and Paolo's DELIVERY photo read by the real PaddleOCR result = a pending
delivery order (green bubble + Priority card).
**Checks:** t25 (settle/remove/restore, hidden lists, real receipts -> cash/bank, NetBank passbook, viewer deliveries,
transfer both ways twice), the security suite, t22, every GET route on the mock data and on the owner's test database
(migrations), the same-totals check, and a browser walkthrough of every new screen.

## Round 14
**Payments = cash or bank, no bank accounts:** the page no longer shows / adds bank accounts (`pay_accounts` and the
sales-bank setting stay only so older data opens; legacy account lines count as bank). `pay_entries.method` 'cash' |
'bank' (NULL = old row: no account = cash, an account = bank; `method_of()`). Every order payment now writes BOTH the
`bulk_order_payments` row (Sales sees it) and a `pay_entries` line with its method: `record_order_payment()` used by
`POST /orders` (`paid` + `paidBy`), `POST /orders/{oid}/payments {amount, note, date, method}` and
`POST /payments/entries {method, amount, date, note, orderId}`. Old order payments without a line = "Recorded in Sales"
(counted as cash) -> `POST /payments/link?paymentId=&method=cash|bank`. `PUT /payments/entries/{eid}/method {mode}`
switches one (and rewrites the leading Cash/Bank word of the Sales note). Deleting an order deletes its account-less
lines. `GET /payments` -> `cash`/`bank` {count,total} (all time), `cashMonth`/`bankMonth`, `orders`, `owed`, `banks: []`.
`GET /payments/list?kind=cash|bank&month=YYYY-MM` -> `payment_rows()`: sales grouped per day per sales person, one line
per order payment, other money in; newest first with `soFar` (running total), `total`, `allTotal`, `months`.
`/payments/day` labels are "Cash" / "Bank" ("Recorded in Sales" for not said). Frontend `src/pages/payments.jsx`: Payments
by day, Cash payments, Bank payments (`PayList`: date | name | amount | total so far, month switcher, a click opens that
day), Incomplete & missing / Completed payments (`PayOrder` with a Cash | Bank switch). Sales: the order form's down
payment and the order's payment log have Cash | Bank too.
**Cash or bank from the photo (new rule):** CASH SALES ticked -> cash; a bank word (incl. "PAID IN BANK") -> bank;
"paid in cash" / COD / "terms: cash" -> cash; a PAID stamp (`PAID_STAMP_RE`: a short box that STARTS with paid/pa1d/pald;
"Amount paid" does not count) -> bank; nothing (also CASH SALES not ticked without a stamp) -> cash, sure=False.
**OCR rows that are never items:** `build_lines_full` skips a PAID stamp row ('PAID' looked like 'Pail') and, when the row
has no quantity, `NOT_ITEM_ROW` (the shop's name "TRES NINOS HARDWARE", "construction supply", CASH / CHARGE SALES,
"sales invoice", "delivery receipt", "received", "thank you", VAT reg...). Quantity units: `SHORT_QTY_RE` = m, mtr(s),
lit, ltr(s), gal(s), btl(s), bot(s), bottle(s), pair(s), pack(s) only after the LAST number of the row (so 'Steel bar 12mm
6m 15' keeps 15) and not right after 'x' ('2 x 10 Mtr.' is a size); a can unit is kept in the item text for matching
('3 gal Lacquer thinner' -> 'Lacquer thinner gallon'). A printed amount at the END of a row ('... 180.00') is never
the quantity (before, 'TOTAL 5700.00' got qty 5700 and '2 btl Paint thinner 180.00' qty 180). `norm`: lit/ltr -> liter, gal -> gallon, btl/bot -> bottle,
'1L' -> '1 liter'; `numbers_in` ignores a number before liter / gallon (the can, not a size). Sizes: when the text gives
only PART of the item's numbers ('boots size 43' vs 'Size 43 (#9)', 'cyclone wire 5ft 2x10' vs '5 Ft. 2 x 10 Mtr.') the
penalty is a third and it still shows as "Please check". Regression: parse_baseline (51 rows) unchanged except 'Paint
white 1 gallon' -> item 'Paint white gallon', 'TOTAL 5700.00' / 'Sub total 1,200.00' -> no quantity; the delivery photo
match unchanged (strict mode now also finds C-Purlins). Real PaddleOCR on a receipt with a turned red PAID stamp: the
stamp is read as 'PAID' -> bank; the shop name and the stamp give no item rows.
**Cost = 65% when not given:** `items.cost_auto` (1 = assumed). `shop_db.COST_SHARE`, `assumed_cost(sell)`,
`cost_of(cost, sell)`. Start-up migration: items with cost <= 0 and sell > 0 get 65% + cost_auto=1. Supply: a type added
without a cost gets 65%; PATCH with the same assumed cost and a new selling price -> recomputed; a typed cost -> real
(cost_auto 0); 0 -> assumed again. `type_dict.costAuto`. `commit_sale` uses 65% for an item with no cost. Transfer
carries `cost_auto`; an imported item with no cost gets 65%.
**Restock fund only for critical items + Auto inventory:** `auto_inventory(conn)` = every item whose `stock_level` is
C/B/A (items without levels only when out of stock), with units sold since the last purchase (else since the 1st),
`saved` (units x cost, 65% fallback), `buy` = max(units sold, top level - qty + 1), `buyCost`. `GET /auto-inventory`;
`/savings-plan` -> `restock` = those with sales, `auto`, `autoCount`, `autoCost`. UI: cashflowGraph.jsx `ToSave`:
"Auto inventory" button above the RESTOCK FUND (modal grouped A / B / C), level tags on restock rows.
**Receipt delete by the owner:** `server.remove_receipt(conn, r, revert)` (shared with the phone's DELETE): 409
`has_sales` unless revert; revert puts the stock back and removes the sale; a pending delivery draft of the photo is
deleted (handled ones lose the link); photo + thumbnails removed. `DELETE /api/shop/receipts/{rid}?revert=` via
`build_router(..., drop_receipt=)`. UI: trash on each receipt in Sales -> counter -> Receipt, "Delete receipt" in the view.
**Add Order:** payment at the bottom (order total + delivery cost together, Paid now with Cash | Bank, unpaid balance).
Delivery runs: "Split the N trips over the days" (`SplitTrips`: days from the target start, count = the target window,
2..14, trips spread front-loaded 3/2 -> 2+1, editable) -> one run per day with the same truck / driver / helpers (first
keeps the run id). Trip suggestion counts each truck once; a truck split over days is never auto-changed; "Trips per
day" chips under the runs. No backend change (runs were already a list).
**Transfer:** `diff()` compares numbers as numbers (198 == 198.0). Log lines (`add_lines`), order payments in the plan
and the account-less payment lines (`line_key`: day, amount, in/out, party, order) are COUNTED, not merged, so two
equal payments on one day stay two. An employee `login.password_hash` (PBKDF2 only) is used for a phone login that has
no password yet (`!moved-no-password`) - only starter files carry it; normal exports never do. Plan label "phone login
@user (password set)".
**Starter file (real data):** `starter/TresNinos_START_real_inventory.zip` (made outside the repo from
Hardware_Inventory.docx): 46 products / 138 types (Boysen = one product, each color x Liter/Gallon a type), selling =
cash price, cost 65% (cost_auto), levels liters 5/10/15, gallons 2/4/6, bottles 10/20/30, none for pcs/rolls/meters/
pairs/packs; per-meter items stocked in meters (rolls x length); 7 sales persons (position Sales Person) with phone
logins (usernames kept out of this guide; passwords only as PBKDF2 hashes). Duplicate sizes in the
list got "(other)": Cyclone Wire 5 Ft. 2 x 10 Mtr. ₱4,150, Welded Wire G-19 1 x 3 Ft. ₱85. Chicken Wire has no count (0);
Tie Wire 16/18 whole rolls have no price (0).
**Checks:** t26 (starter apply + logins, 65% cost, auto inventory, PAID stamp -> bank, cash/bank lists, order payments,
receipt delete with revert, transfer with equal payments both ways), t25 on the new rule, t22, every GET route on the
owner's test database copy, parse/match regression, and a browser walkthrough (ui20).
## Round 15
**Invoice table reader (ocr_engine.py):** `read_invoice(boxes)` -> `find_item_table` (header row: a DESCRIPTION /
ARTICLES / PARTICULARS / ITEM token (`head_kind`: regex, or fuzzy >= 80 for long words: 'DESCRIPTI0N', 'Amonnt') plus a
Price or Amount token within 1.6 header heights; the 'Unit' printed above 'Price' (overlapping x, or 'Unit Price' in one
box) belongs to the price column; columns reach halfway to their neighbours; body = header bottom .. first `FOOTER_RE`
box (VATable, Total Sales, TOTAL AMOUNT DUE, Less:, received...) minus 0.4 x the median height) -> `table_lines`
(boxes by centre x into columns; a multi-word box over several columns is split word by word, words without digits that
fall in Price/Amount stay in the description) -> `table_entries` (a line with Qty/Unit starts an item, a description-only
line continues it, a price/amount written lower fills it, numbers-only lines are notes) -> per entry `qty_cell`
(lookalikes: lone stroke l/I/|/+/T/F = 1, 'to'/'lo' = 10, fractions, '2pcs'), `unit_word` (UNIT_CANON -> Supply unit
names), `money_cell` (DIGIT_LOOKALIKE o/O/D/Q->0 l/I->1 s/S->5 a/g/q->9 B->8 z->2 b/G->6 T->7; letters-only values are
kept only when another number agrees), '@260' in the description = price; qty from amount/price, price from amount/qty;
`invoice_total` (TOTAL AMOUNT DUE > grand total > total) fills the one missing amount; notes x - y = total -> `paid` /
`change` (or one round number <= total + 1000). `invoice_lines(inv, items)`: `rank_matches(desc, items, loose=True)` +
`price_fit` (+30 within 1% / max 1 peso, +10 within 10%, -25 otherwise, -15 when the item has no selling price; no qty =
the amount taken as the price of 1; a positive fit only for candidates within 15 points of the best text score, and at
most +10 when the product word is weak); several items with the same price that read alike -> capped below AUTO_OK
("Please check"); no candidate >= MIN_SHOW -> unmatched with `tidy_name` (words of the 3 nearest items >= 65, then all
item words + HARDWARE_WORDS >= 80), qty (1 when only money was read), price, unit. `build_lines_full` uses this path
whenever such a table is found (phone receipts and Add Order "Read from photo"); delivery receipts (ARTICLES without
price/amount columns) keep the old row-by-row path. Lines now carry `price`, `unit`, `item` (the description).
**Old path filters:** `JUNK_ROW` (always skipped: shop name/address, form labels Sold to/Address/TIN/Terms/OSCA/PWD,
footer labels, BIR / Authority to Print / Date Issued / business forms / Bklts / Tel. / receipt number, a lone 'Label:')
and `header_only()`; `parse_line` returns no quantity >= `QTY_MAX` (100000).
**Second look (PaddleOCR):** `read_image` stores each box's `score` (rec_scores). In the OCR worker `_do_ocr` calls
`reread_cells(ocr, path, boxes)`: for each table item with an empty Qty cell, the cell (x: the Qty column, y: the line
+-0.8 letter heights, following the line's tilt) is cut from the photo, ruled rows (horizontal opening) and the printed
column line (a component as tall as the cell) are wiped, the remaining ink is read with
`ocr.paddlex_pipeline.text_rec_model` alone; a quantity read (or one tall thin stroke) becomes an extra box
`{'src': 'reread'}`. Real photos: the missed '1' of both invoices is found ('I' 0.97, 'T' 0.34 -> 1). Never raises.
**Payment:** after bank words / paid in cash, a change note (`read_invoice` paid/change) -> cash, sure, "₱500 received,
₱170 change written on the receipt"; then the PAID stamp rule.
**Receipt rows -> Supply:** columns `receipt_lines.price`, `receipt_lines.item_text`, `receipt_unmatched.price`,
`receipt_unmatched.unit` (stubs for the receipt tables are now in shop_db.RECEIPT_STUBS). `GET /api/shop/receipts/{rid}`
-> `lines` (+ itemId, price (on the receipt), sellPrice, ocr, checked, sure, qtyUnknown, suggest = tidy name for unsold
rows) and `unmatched` [{id, item, qty, price, unit, ocr}]; the receipt list adds `notInSupply`, `notInSupplyTotal`.
`POST /api/shop/receipts/{rid}/rows/{u|l}/{row_id}/item {itemId}`: an unmatched row becomes a verified receipt line
(score 100), or an unsold line points to another item (409 when sold). UI: sales.jsx receipt view "NOT IN SUPPLY" with
**Add to Supply** (and "Not this item?" on unsold lines) -> `/supply?add=&price=&unit=&qty=&receipt=&row=u12&label=&ocr=`
-> supply.jsx opens the Add form filled in (banner "From receipt ...") -> after Apply links `created.types[0].id` and
navigates to `/sales?receipt=<id>` (the Sales page opens that receipt).
**Quantity N/A:** `items.qty_unknown` (TypeIn.qtyUnknown; stored quantity 0). `ocr_engine.qty_unknown(it)`: stock_check /
annotate_lines never "short", `commit_sale` does not deduct (and a reverted sale does not add back). `type_dict`
qtyUnknown (level None). Excluded from `/stats` lowStock, `auto_inventory` and the A/B/C priority cards; instead
`/priority` has `count-<id>` cards (kind "count", sticky: no X, `/priority/dismiss` -> 409) with "N sold since".
`POST /supply/items/{iid}/count {qty}` sets the count (qty_unknown 0); `/adjust` also clears it. Supply's count box uses
/count. Transfer carries `qty_unknown` (ITEM_F). UI: TypeRow N/A checkbox + amber N/A stock cell, NaBadge on cards,
"Not counted (N)" filter chip, main.jsx `CountFix` in the priority modal.
**Supply search:** supply.jsx `matchesSearch` (every typed word in the name or a type's spec/unit), clear with X / Esc.
**Who delivers:** `bulk_order_runs.mode` ('truck' | 'pickup' | 'other'; NULL = truck), `carrier`, `carrier_cost` (per
trip). `RunIn` mode/carrier/carrierCost: pickup/other runs keep no transport and no crew (`set_crew(None, [])`); a truck
run without a truck is dropped. `run_cost`: truck = transport cost x trips, other = carrier_cost x trips, pickup = 0.
`run_dict` adds mode, carrier, carrierCost, cost. `PUT /runs/{rid}` takes mode (back to truck picks the old or first
truck; 409 when there is none), carrier, carrierCost; crew changes only for truck runs. Planner: run `truck.name` =
"Customer picks up" / the carrier for the others; day stats `pickups`, `others`, noDriver/noHelper/trucks count truck
runs only; `/planner/day` returns `transports`. Priority: "Pick-up today: ..." / "Delivery today: ... Delivered by X".
Calendar + saved-file deliveries show who delivers (`mode`). Transfer: runs carry mode/carrier/carrier_cost; the run
key is day + truck (ours) or day + mode + carrier (`file_run_key`).
**Order total = items + delivery:** `bulk_orders.charge_delivery` (new orders 1 unless chargeDelivery=false; NULL/0 for
orders before r15). `order_money(conn, o)` -> items, delivery, charged, total; `order_dict` adds itemsTotal,
deliveryCost, chargeDelivery, deliveryCharge and total (items + charged delivery) - used by /orders, complete (Done needs
the total paid), sales-summary, order_state (Payments) and the saved-file order lists. `OrderIn.chargeDelivery` (None =
unchanged on edit). Transfer carries charge_delivery (ORD_F). UI (sales.jsx): the order form's delivery section is a
summary + **Add delivery / Edit delivery** -> a popup (Modal inside the form) with the load info, the runs
(`DeliveryRunRow` with `ModeSwitch` from components/crew.jsx), trips per day and the delivery cost; the bottom shows Order
total, Delivery (+ "The customer pays the delivery" checkbox), **Total**, Paid now (Cash | Bank) and Unpaid = Total -
paid. New orders start with no run ("Add delivery"). Order card / profile use the server total; the profile lists who
delivers each run and Items / Delivery / Order total / Unpaid. Planner `RunCard`: WHO DELIVERS switch, truck select,
carrier + cost fields (saved on blur / Enter), crew only for our truck, "Mark picked up".
**Checks:** t27 (real invoice boxes -> rows / not in Supply / cash change; Add to Supply with N/A -> linked -> sold without
stock -> count card sticky -> counted; wrong row relinked; delivery modes, totals with and without the charge, Planner
stats, run mode switching, Done needs the delivery paid, calendar, setup file both ways = t27b), t27_ocr (made-up
layouts: 3 items + 2-line description, no qty column, one box over 3 columns, same-price colours, other price, empty
invoice, notes, ARTICLES + AMOUNT, typo header + '@price', no header at all), t26 / t26b, t25 / t25b, t22, every GET
route on the owner's database copy and on the real inventory, parse/match regression (unchanged), real PaddleOCR second
look on both photos, browser walkthrough (ui21, ui21b).

## Round 16
**Orders take their items out of Supply (shop_api.py):** `bulk_order_items.stock_taken` (REAL; how much of that row left
`items.quantity`; NULL = a row saved before r16, it takes its stock on the order's next save). `takes_stock(row_unit, it)`:
only rows picked from Supply (itemId), in the item's own unit (or no unit), item not N/A (`qty_unknown`).
`plan_stock(conn, oid, items, take)` -> (takes, before, short); what the order already took counts as available to it.
`write_order_rows(conn, oid, items, runs, take)` (create + edit) applies only the difference per item
(`UPDATE items SET quantity = quantity - delta`) and stores `stock_taken` per row; too little in Supply ->
`not_enough_error(short)` = HTTP 409 `{"detail": {"message": "This is our only supply: <name> has <have> <unit> left, the
order asks for <want>", "short": [{itemId, name, unit, want, have}]}}`. Not taken: orders made from a phone's DELIVERY
receipt (`order_from_receipt(conn, oid)` = a `delivery_drafts` row points to it; `apply_draft` -> `create_order(...,
take=False)`; the phone's Update of that receipt sells it already). `DELETE /orders/{oid}` gives back `stock_taken` of
rows not delivered yet and returns `backToSupply` (count). `order_dict`: items carry `stockTaken`, the order
`fromReceipt`; the edit / add notes say "· from Supply: 50 liters Boysen ...". `auto_inventory` counts order rows with
stock_taken > 0 as sold (velocity). Transfer: order rows carry `stock_taken` (kept only when the item exists).
**api.js:** a failed call keeps `err.status` and `err.detail`; a 409 whose detail has `short` is NOT toasted (the page
shows the popup).
**Order form (sales.jsx):** the item row's "weight kg" input is gone (kgPerUnit stays in the data; trips are suggested
from kg/ton units or a weight in the name). `OrderForm({checkStock})`: per row the stock still available (Supply +
what this order took when editing); leaving the Qty box with more than that, and Apply, open `OnlySupplyModal` ("This is
our only supply": **Use the N <unit> we have** sets the quantities to what is left / removes rows with nothing left, or
**Change it**); a 409 `short` from the server opens the same popup. `checkStock={false}` for DraftOrderModal and for
editing an order with `fromReceipt`. Delete asks "Back to Supply (not delivered yet): ...". The order profile shows
"· out of Supply".
**Add delivery = our truck only:** `DeliveryRunRow` has no mode switch any more (label "Our truck"); a pickup / other run
made in the Planner is shown read-only ("Who delivers was set in the Planner") and kept on save. **Planner:**
`PlanOrderRow` (planner.jsx) for each `ordersWithoutRun`: `ModeSwitch` (default pickup) + day ->
`POST /api/shop/orders/{oid}/runs` `RunNew {mode, date, transportId?, carrier?, carrierCost?, trips?}` (422 bad mode,
409 order done / no truck yet; our truck = the first truck when none is given).
**Receipt reader (ocr_engine.py), measured on the 78 photos of the owner's "to copy" folder (scratch truth table, 204
item rows):** wrong item matches 19 -> 0, rows found 127 -> 159, qty right 70 -> 123, price right 74 -> 108, extra rows
40 -> 10, totals 25 -> 43 of 70, paid/change 3 -> 14 of 40, payment mode 18 -> 20 of 20.
- *Upright:* `read_upright(ocr, path, side)` (used by the OCR worker instead of read_image + reread_cells): `page_turn`
  decides 0/90/180/270 from the boxes (tall thin lines = sideways; the biggest lines are read both ways with the text
  recognizer alone and the clearer way wins; upside down = reads better turned round), `turn_photo` rotates the file
  itself, then it is read again. The worker reports `turned`; server.py deletes that receipt's thumbnails
  (`THUMB_DIR/<tag>_*.jpg`) before `_store_result` so they are made again from the upright photo.
- *Matching:* `item_keywords(name)` = (product words, colour words); `COLOR_WORDS` never make a match ("wheel barrow
  yellow" is not "QDE Lemon Yellow"). `kw_sim` (short words like QDE/PVC/RSB/GI must be nearly exact; `CODE_LOOKALIKE`
  maps digits in codes: RS3 = RSB), `keyword_score`, `word_coverage(text, name, need)` = (share of the item's product
  words in the text, share of the text's product words in the item). `units_fit` (`UNIT_FAMILY`: liquid / dry /
  length / roll / sheet / pair): a receipt unit of another family excludes the item. `rank_matches(text, items, loose,
  unit, kw_min)`. `invoice_lines`: kw >= KEYWORD_MIN unless the price is exact (then kw_min 70: 'adhoscire'); another
  price only with kw >= 90 and every item word present; no price to confirm -> every item word present (or kw >= 90 and
  both coverages >= 0.5); one word in common is not enough. Same-price twins -> "Please check".
- *Numbers:* `money_tol` (fixed 0.6 peso: pesos are rounded), `nice_qty` (whole, or 1/4, 1/2, 3/4 below 1, or 1.5),
  `one_digit_off`, `QTY_AS_FRACTION` ('14' = 1/4), `repair_row(q, q_read, p, a, locked)` mends ONE number in the order
  amount -> price -> qty, `split_glued` ('7519' = 75 | 19 at the column line), `_settle`. `read_invoice`: the TOTAL is
  trusted when the notes agree with it (paid - change = total) or the rows add up to it; a misread total is replaced by
  the sum of rows that all check out; amounts are locked when they already sum to the total. `desc_money` reads
  'Tubular 2x6 1.5 (2000) = 20,000' (delivery tables without price columns: `find_item_table` `money` flag; a missing
  description header = the widest gap between the found columns). `_noise_line` drops stamps across the table ('OU',
  'CIUEVIO'). `note_numbers` / `pay_pair(nums, total, single)` / `money_notes` -> paid / change (circled money in the
  empty rows or VATable boxes; footer values and printed numbers are not notes). `is_footer` / `FOOTER_PHRASES` (fuzzy).
- *Names of rows not in Supply:* `tidy_name` with `ocr_word_sim` (`_CONFUSE` letter pairs cost less), `SIZE_TOKEN`,
  `STOP_WORDS` (small words stay), `HARDWARE_WORDS`: 'TIVE AOHFLLIVE' -> Tile Adhesive, 'RS3 lomm' -> RSB 10mm,
  '6 Bap arpour TIpVellow' -> Armour Tip Yellow.
- *"Is it ...?":* `maybe_item(text, price, unit, items)` (shares a product word, unit fits, sells at exactly the written
  price; no price: kw >= 85 and half the item words). `GET /api/shop/receipts/{rid}` -> each `unmatched` row may carry
  `maybe {itemId, name, unit, sellPrice}`; sales.jsx shows "Is it <name> (₱price)? **Yes, it is**" ->
  `POST /receipts/{rid}/rows/u/{row}/item {itemId}` (the r15 link route).
- Old receipts keep their stored rows; Read again (`/receipts/{rid}/read-again`, refused when part of it was sold)
  reads them with the new reader.
**Checks:** t28_orders (stock taken on create, difference on edit, 409 short with what the order already took, N/A /
other unit / typed rows not taken, delivery-receipt orders not taken, delete gives back undelivered rows, Planner
`POST /orders/{oid}/runs` modes and errors), t28_match (colour-only, unit family, coverage, codes, twins, maybe_item),
t28_receipt (worker `turned` -> thumbnails remade, unmatched `maybe` + link), smoke (every GET route incl. the shop
router), evalr on the 78 sample photos (scores above), browser walkthrough ui28 (popup on blur and on save, Use what we
have, truck-only popup, edit 60 -> 50 gives 10 back, edit over the stock -> popup, Planner NO DAY YET row, receipt view).

## Round 17
**Delete with history (shop_api.py):** `items.deleted_at` (TEXT). `drop_item(conn, iid)` (inside the caller's
transaction): unsold `receipt_lines` of the item -> `receipt_unmatched` rows (same text/qty/price); open orders' rows ->
`item_id NULL` (typed rows); `supply_sourcing` deleted; with history (`item_history`: sale_items, receipt_lines,
stock_purchases, bulk_order_items) the row stays: `deleted_at=now, name='<name> (deleted)'` (`free_item_name` adds ' 2'...
so the UNIQUE name is free again), `product_id NULL`, levels cleared, qty_unknown 0; without history it is really deleted
(+ its stock_purchases). `DELETE /supply/items/{iid}` and `/supply/products/{pid}` -> `{deleted, keptHistory}`. Every
current-use query skips deleted items: `ocr_engine.get_items(conn)` (matching, phone /api/items, maybe_item) -
`get_items(conn, everything=True)` only in stock_check / annotate_lines / commit_sale; /stats lowStock, /priority,
auto inventory, ensure_products, product_dict, plan_stock, link row -> item, edit / count / adjust / purchase / sourcing
(404), server `need_item` (422). History JOINs need no change (the row exists). Transfer: ITEM_F has `deleted_at`;
`item_for` creates '(deleted)' names hidden.
**New items from the phones:** `receipt_unmatched.req_name, req_price, req_qty, req_unit, req_by, req_at` (req_at NULL =
plain row). Phone (server.py): `DELETE /api/receipts/{rid}/unmatched/{uid}`, `POST .../unmatched/{uid}/request
{name, price, qty, unit}` (`NewItemIn`, 422 on name < 2 chars / price or qty <= 0), `POST /api/receipts/{rid}/requests`
(ocr_text '(added by hand)'), `GET /api/units`. `detail()` unmatched rows: id, ocr_text, item, qty, price, unit,
request {name, price, qty, unit, at}|null. `_store_result` (read again) keeps request rows and skips new unmatched rows
with the same ocr_text. Owner (shop_api.py): `GET /requests` (`request_dict`: receiptLabel, by, amount, sellNow = no
other unsold line on the receipt, payMode/payNote of the receipt, maybe = maybe_item or the same name in Supply),
`POST /requests/{uid}/accept` (`AcceptIn`: name, unit, sellPrice, costPrice (empty = 65%), stock (after this sale; None =
N/A), itemId (link instead), payMode) -> new product + item (qty_unknown 1) or link; a verified receipt line (req
qty/price); if sellNow: `commit_sale` for that line for the receipt's user (sales.pay_mode = the receipt's choice, else
payMode, else cash); then the typed stock is set. 409 when the name exists. `DELETE /requests/{uid}` removes the row.
`/priority`: sticky card `req-<id>` (kind "request", score 230; dismiss -> 409). Receipt view: unmatched rows carry
`request {..., by}`; `POST /receipts/{rid}/rows/u/{id}/item` uses req_qty/req_price for request rows. UI: supply.jsx
`RequestCard`, `AcceptRequestModal`, section "New items from the phones" below the grid, `/supply?request=<id>` opens it;
main.jsx request cards -> "Supply" button; sales.jsx receipt view shows "<who> sent it as a new item" + link (no "Add to
Supply" on those rows).
**Payment from the phone:** `detect_payment`, `tick_marks`, `paid_stamp`, the CASH/BANK regexes were removed from
ocr_engine (PAID_STAMP_RE stays: a PAID stamp is never an item row). `_store_result` no longer sets receipts.pay_*.
`CommitIn.pay_mode` ('cash'|'bank', 422 otherwise) -> receipts.pay_mode / pay_note "chosen by <name> on the phone" /
pay_sure 1, and the sale's pay_mode; no pay_mode (older phone app) -> cash, note "not asked (an older phone app)".
`payment.mode` is null in /api/receipts/{id}, /api/shop/receipts/{id} and delivery drafts until chosen (PaidTag: "not
chosen yet"). `read_invoice` still finds paid/change notes (they help trust the TOTAL), they are not shown.
**Phone stock info:** `detail()` lines add `need` (all unsold rows of the item on the receipt) and `stock_known`;
/api/items adds `sell_price`, `stock_known`.
**Orders:** `POST /orders/{oid}/finish {method, date}` = all items delivered + all runs done + the unpaid balance as one
payment (record_order_payment) + status done (409 when done, 422 bad method) -> {id, customer, total, completeDate,
paidNow, deliveredNow}. `PUT /runs/{rid} {done: true}` on the last open run of an order ticks all its items delivered.
UI: `FinishOrderModal` ("Complete delivery & payment…" when not complete).
**Truck only:** `PUT /runs/{rid}` mode other than 'truck' -> 422 ("truck" converts an old run); `POST /orders/{oid}/runs`
only mode 'truck' (transportId or the first truck); `DELETE /runs/{rid}`; `/planner` returns `transports`. planner.jsx:
RunCard without the mode switch (old pickup/other runs: "Use our truck" / "Remove this delivery"), PlanOrderRow = truck
select + day. Order form keeps old non-truck runs as they are.
**Fast CPU mode (ocr_engine.py):** `_build(device, tier, fast)` -> `enable_mkldnn=fast`. `load_model(device, tier, safe)`:
`fast_mode_wanted` (CPU; env SHOP_OCR_FAST / SHOP_OCR_MKLDNN = 0/1 forces it; `ocr_fast_mode_off.txt` in APP_DIR = off;
a leftover `ocr_fast_mode_trying.txt` = the process died during the test -> off). The fast build must read `_test_image()`
(printed words: det + rec run); an exception -> the plain build, `fast_mode_failed(why)` writes the off file. `FAST_ON`
is reported in model_ready ("fast"); server `OcrService.fast`; a worker crash while fast -> `fast_mode_failed`,
`safe=True` for the respawn (load job ("load", device, tier, safe)); `/api/ping` has `fastMode`. Measured: fast 5-8 s per
photo (2 threads, 1280 px) vs plain 16-23 s at 640-960 px.
**Reader fixes:** `find_item_table`: the description column spans its whole printed heading ('Item Description / Nature of
Service': the tokens after DESCRIPTION in the same box, up to another column's word) - long descriptions no longer spill
into Quantity. `read_invoice`: the one-item "TOTAL is its amount" rule is skipped when the row already agrees with itself
(qty x price = amount, or price | amount give a nice qty: 'Fine Sand | ? | 70 | 280' with the total read as 980 -> 4,
not 14). `invoice_lines`: fit == 10 (price within 10% but not exact) needs kw >= 85. `qty_cell`: '1 PC.' read as 'IRC.'
-> (1, pcs). Scores: the user's 7 new photos (same receipts as samples, truth keys 223202 223119 194950 223215 223000
223148 195027): found 14/14, qty 14/14, price 14/14, wrong 0 (r16: qty 12, price 13, wrong 1); 78 samples: found 160,
qty 122, price 109, wrong 0, extra 8, totals 44/70.
**Phone app (shop_phone_app/lib):** api.dart `units()`, `deleteUnmatched`, `requestNewItem`, `addNewItem`,
`commit(..., payMode)`. receipt_screen.dart: `_askPayment` (Cash / Bank dialog before every Update; the choice is sent
with the partial / skip retries too), `_newItemDialog` (name, price for one, how many, unit + unit chips), `_unmatchedRow`
(New item / delete; "Sent to the owner - waiting in Supply"), `_shortText` ("Only N unit left in Supply"), the add/edit
dialog shows "In stock: N" and warns when more is typed, "Not in the list? Send it to the owner as a new item".
**Checks:** t29_r17 (all of the above incl. the fast-mode switches and the server crash path), t28_* updated (truck-only
plan run, no payment from photos), smoke 62 GET routes, real PaddleOCR fast-mode load + test read + simulated failure,
evalr (78 samples + 7 new photos), browser walkthrough ui29 (dashboard card -> accept, delete with history, Planner old
run -> our truck, finish modal, receipt request line), Dart files syntax-checked with tree-sitter (no Dart SDK here).

## Round 18
**New items are not sold on accept:** `POST /api/shop/requests/{uid}/accept` only adds the item (or links) and puts a
verified, UNSOLD receipt line (req qty / price) on the receipt; the sales person sells it with the phone's Update (Cash /
Bank). `receipt_unmatched.req_stock` = how many the sales person says are left after this sale (phone field "How many
are left in the shop?", `NewItemIn.stock` >= 0); `request_dict` has `stock`; the accept form pre-fills it. A new item
gets `quantity = stock + the row's qty` (the Update takes the row's qty off -> the stock said), or N/A when no stock.
`sellNow` / `payMode` are gone from the request API.
**Truck trips of their own:** `bulk_order_runs.order_id` may be NULL (shop_db `_allow_trips_without_order` rebuilds the
table once on old databases, same ids; run_crew unchanged) + columns `purpose` (deliver | get | personal), `place`,
`note`, `time` (HH:MM). `POST /api/shop/trips {purpose, place, note, date, time, transportId, driverId, helperIds}` (422
bad purpose / time, 409 no truck). `PUT /runs/{rid}` also takes purpose / place / note / time for trips; `DELETE
/runs/{rid}` removes one. `run_dict`: isTrip, purpose, place, note, time, label ('Get materials: <place>'). Planner
month / day / unscheduled / next, crew busy lists, the employee's coming runs, calendar day, saved-data day and the
dashboard ('trip-<id>-<day>' cards, today / tomorrow) include trips (LEFT JOIN bulk_orders, label as customer). The crew
pool has `others` (every active employee who is not a driver / helper): they can be driver or helper; day stats
`staff` / `staffFree`, `freeStaff`. UI: planner.jsx `TripForm`, `TripCard`, `PurposeSwitch`; crew.jsx optgroups
"Drivers" / "Labor / helpers" / "Other staff".
**Checks:** t30_r18 (migration on the user's database, trips, crew from other staff, Planner / dashboard / calendar /
employee runs, orders untouched), t29_r17 updated (accept does not sell; stock + qty), all older tests, smoke, browser
ui30 (accept window with the stock said, + Truck trip with a sales person as driver), Dart syntax check.

## Round 19
**Downloads in the .exe:** WebView2 (pywebview) did not show `<a href>` downloads. app_launcher.py passes
`js_api=SaveFiles()` (+ `settings["ALLOW_DOWNLOADS"] = True` as a fallback, `text_select=True`):
`save_download(link)` accepts only `/api/...` paths, fetches them from 127.0.0.1:8000 into a temp file, takes the name
from Content-Disposition, opens `create_file_dialog(FileDialog.SAVE | SAVE_DIALOG)` in the Windows Downloads folder
(SHGetKnownFolderPath), moves the file there and runs `explorer /select,`. Returns {ok, path} | {ok:false, cancelled} |
{ok:false, error (server detail)}. Frontend: lib/api.js `inAppWindow()`, `saveDownload(path)`, `announceSaved()` (green
toast, event `shop-saved`, AppShell ErrorToast); components/kit.jsx `DownloadLink` (plain link in a browser; in the
.exe preventDefault + "Preparing…" + save dialog). Used by Calendar day, archive, Transfer export, restock CSV. Any new
download button must use DownloadLink.
**Restock list:** shop_api `low_stock(conn)` (shared with /stats lowStock; adds `buy` = top level - stock + 1 for whole
numbers, None without levels), `GET /api/shop/restock` {at, items}, `GET /api/shop/restock/download` (admin_q, CSV with
empty "bought qty" / "price paid"). supply.jsx `RestockModal` (A / B / C filters, Print through a hidden iframe,
`restockHtml`), button "Restock list" next to Add Item. Removed the "paid price below the standard cost is extra
profit" note under Buy stock.
**Checks:** t31_r19 (restock = dashboard list, buy lifts above the top level, CSV, 401 without token), t_launcher
(SaveFiles with a fake dialog: saved / cancelled / server error / non-api path, no temp left), all older tests, smoke 64
GET routes, browser ui31 (restock modal, print frame, CSV download, app-window downloads with a fake pywebview api).

## Round 20
**Restock totals:** `low_stock` items have `total` = buy x cost_price (None without a buy suggestion). The CSV has
"standard cost" / "total (buy x cost)" with 2 decimals (`money()`), TOTAL A / B / C and TOTAL (all) rows. RestockModal:
Standard cost + Total columns, "Total to buy"; print: per-level total row + grand total.
**Counter cards (sales.jsx):** the name box is always 2 lines (`h-[2.25rem]`, line-clamp-2) and the buttons sit in a
`mt-auto` block, so every card lines up.
**Receipt folders:** Calendar day ZIP `receipts/<person>/<label>_<id>.jpg`; archive
`receipt_photos/<YYYY-MM>/<person>/...` (`folder_name()` makes a Windows-safe name, "No name (phone)").
**Phones on a new laptop:** server.py `allow_phones()` (the old body of /network/fix: Private network + firewall rules
for the exe, TCP 8000, UDP 8765; raises PermissionError / TimeoutError) used by `POST /api/shop/network/fix` and by
app_launcher `first_start_phone_check()` (thread at start: if `phone_problems` or the firewall cannot be read, asks
Windows once; remembered in APP_DIR/phones_allowed_<md5 of exe path>.txt, also when answered No; not on timeout).
`/api/shop/network` has `firewallUnknown`; the profile's phone card always shows the fix button on Windows.
app_launcher `keep_awake()` = SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED) while the window is open.
**Checks:** t32_r20 (totals + CSV, per-person folders incl. a name with / : ?, archive, network on Linux), t_launcher2
(first-start check: asked once, timeout retried, No remembered, already fine, firewall unreadable), all older tests
(t28_orders made month-independent), smoke, browser ui32 (cards aligned, restock modal and print).

## Round 21
**Inventory list (CSV) import/export:** shop_api `inventory_csv_doc(raw)` turns a CSV (utf-8-sig or cp1252; `,` `;` or
tab; header row found anywhere; aliases in `INV_ALIASES`) into a setup document with only `sections.supply`
(item name = `item_name(Product, Type)`, empty cost -> `assumed_cost` + cost_auto 1, empty stock -> qty_unknown 1,
duplicates and non-numbers -> 422 "Row N: ..."). `POST /transfer/read` accepts a .csv/.txt upload: converted and
stored as the usual token ZIP, so plan/apply are unchanged. `GET /transfer/inventory-list` (admin_q) = Supply in the
same columns (round-trips as all "same"). transfer.jsx: file input accepts .zip,.csv + "Inventory list" DownloadLink.
The new_inventory.docx (Nov 3) was turned into 253 types: scratchpad inv/make_list.py -> CSV -> setup ZIP.
**Checks:** t33_inv (CSV read/apply on the test DB: 253 new, nothing changed; the ZIP reads back all same; the
inventory list round-trips; bad lists give the row), all older tests, smoke 65 GET routes, build.

## Round 22
**Phones' own Wi-Fi (Windows Mobile Hotspot):** the shop's only Wi-Fi is a TP-Link extender; the log showed no phone
request reaching the laptop on it (Windows firewall fix had succeeded). server.py `_HOTSPOT_PS` (Windows PowerShell 5.1
WinRT NetworkOperatorTetheringManager from the internet/WLAN connection profile; env TN_ACTION status|start|stop,
TN_SSID, TN_PASS; prints one JSON line; run with -EncodedCommand) via `hotspot_call()`; `HOTSPOT_ERRORS` = plain text
for TetheringCapability / TetheringOperationStatus names. Settings `hotspot_ssid` (TresNinos-Shop), `hotspot_password`
(tresninos####, made once), `hotspot_auto`. `GET/POST /api/shop/hotspot` (desk only; POST {on, auto?, ssid?,
password?}; first on makes the firewall rules through allow_phones if 'Tres Ninos Shop (phone Wi-Fi)' is missing;
off by hand clears auto). `start_hotspot_watch()` (lifespan): every 60 s, if auto and off -> start. allow_phones adds
'Tres Ninos Shop (phone Wi-Fi)' TCP 8000 / '(phone Wi-Fi finder)' UDP 8765, -Profile Any -RemoteAddress
192.168.137.0/24; firewall_state ignores those rules. AppShell `PhoneWifi` card in the profile window. The phone app
needs no change (it scans its own /24 and broadcasts there).
**Checks:** t35_hotspot (pretend Windows: on + firewall once + auto saved, name/password checks, rename, off clears
auto, Windows error text), t35b (watch starts it), the PowerShell script parsed with pwsh 7, older tests, smoke 66.

## Round 23
**Rare words in matching (ocr_engine):** `item_words(name)` (4+ letters, not unit / stop / PLAIN_COLORS words; shade
names like emerald, mahogany, caramel DO count), `word_counts(items)` (cached by the names), `rare_words(text, items)` =
item words found in at most max(RARE_MIN=3, 1% of the items) items that the text contains (exact, or kw_sim >= 85 for
5+ letters). rank_matches: an item holding a rare word of the row passes the product-word gate (kw >= 90, score >=
MIN_SHOW) and ranks first (rank tuple = (hits, sc, raw)); `can_fits` = a row saying Lit / Gal / Btl never matches the
other can (`can_word`, item name or unit). invoice_lines: an item holding every rare word of the row counts as
covered; a "twin" (same price, close name) must hold the same rare words; the ONLY item holding the row's rare words
with no contradicting price is OK (AUTO_OK). Plain colours never count (yellow, green, white...: 'wheel barrow yellow').
**build_lines_full crash fixed:** a rejected match (best = None) was still appended (TypeError on non-invoice photos).
**Fast CPU mode:** the server only blames the fast mode when the OCR process dies WHILE reading (inflight not empty);
closing the program used to switch it off for good (this laptop: ocr_fast_mode_off.txt since Oct 28, ~35 s a photo).
An off-file with FALSE_OFF_REASON gets one second chance (FAST_RETRIED_FILE); a second crash while reading is final.
**Warm-up:** `_warmup_image()` = a DEFAULT_SIDE receipt-like page read at start (the first read of a new size is the
slow one), logged as "[engine] warm-up read N s".
**Docs:** README.md (GitHub write-up: learning path, architecture, web -> desktop, making money, secrets),
docs/NETWORKING.md (beginner networking: extender client isolation, Mobile Hotspot + ICS via ncpa.cpl, limits),
docs/WEB_TO_DESKTOP.md. .gitignore keeps starter/, mock_data/, private/, *.zip, *.xlsx, *.db, net_info.txt out.
Staff usernames removed from the guides. private/erd_pgadmin.sql (not on GitHub): the real schema as PostgreSQL
(39 tables, 35 FK links incl. inferred *_id links), checked on PostgreSQL 16.
**Checks:** t37_rare (receipt #3 rows on the 618-item inventory), t28_match 18/18, evalr 78 receipts identical to
before on the old inventory (0 wrong), on old + 618 items only the duplicate 'Armour TIP Yellow' test item differs,
t29_r17 updated (crash while idle keeps the fast mode; second chance), all older tests, smoke.
