# Tres Niños Shop System

A shop management system built for a real hardware store in the Philippines (Tres Niños Hardware). The sales
persons photograph their handwritten or printed sales invoices with a phone. The shop laptop **reads the receipt
(OCR)**, matches each line to the inventory, takes the stock out, and records the sale. The owner sees sales,
stock alerts, cash flow, staff, deliveries and a planner in a desktop app.

> This repository is also a write-up: **what you need to learn** to build a system like this, **how to earn
> from it**, and **how a web app becomes a desktop program**. The shop's real data (database, receipt photos,
> staff logins, inventory files) is **not** in this repository. See [Keeping secrets out of GitHub](#keeping-secrets-out-of-github).

---

## Contents

1. [What it does](#what-it-does)
2. [Prerequisites: what to learn first](#prerequisites-what-to-learn-first)
3. [How the system is built](#how-the-system-is-built)
4. [From web app to desktop program](#from-web-app-to-desktop-program)
5. [How to make money with a system like this](#how-to-make-money-with-a-system-like-this)
6. [Run it yourself](#run-it-yourself)
7. [Keeping secrets out of GitHub](#keeping-secrets-out-of-github)
8. [More documents](#more-documents)

---

## What it does

| Area | Features |
|---|---|
| **Receipt reading** | Phone photo → PaddleOCR on the laptop → table rows → item, quantity, price → stock deducted. Handles tilted or sideways photos, handwriting slips (`emerlad` = emerald), Liter/Gallon cans, and rare words that point at one item. |
| **Supply / inventory** | Products with types, units, selling price, standard cost, A/B/C stock alert levels, photos, restock list (print / Excel). |
| **Sales** | Per-sales-person counters, receipts, orders with deliveries, cash or bank payment. |
| **Money** | Cash flow, payments, obligations, employee pay, savings when buying below the standard cost. |
| **People** | Employees, attendance, phone logins (one per sales person). |
| **Planner** | Truck trips: deliveries, buying materials, driver and helpers. |
| **Calendar** | Everything that happened on a day; download the day as a ZIP (CSV for Excel + receipt photos per sales person). |
| **Transfer** | Move the setup to another laptop; import an inventory list from Excel (CSV). |
| **Networking** | Phones find the laptop on the Wi-Fi by themselves; the laptop can make its own Wi-Fi for the phones. |

---

## Prerequisites: what to learn first

Read this as a learning path. Each topic lists **where it is used in this project**.

### 1. Programming foundations
- **Python** (functions, classes, modules, virtual environments, `pip`): the whole backend.
- **JavaScript + React**: the desktop pages (`src/`).
- **Dart + Flutter**: the phone app (a separate project).
- **Git and GitHub**: version control and publishing safely.

### 2. Data engineering (the most important part)
| Topic | What to learn | Where it is used |
|---|---|---|
| **Relational databases + SQL** | Tables, primary/foreign keys, joins, `GROUP BY`, indexes, transactions | SQLite database with ~40 tables (`backend/shop_db.py`) |
| **Data modeling / ERD** | Entities and relationships, normalization, one-to-many vs many-to-many | products → item types, sales → sale items, orders → deliveries → crew |
| **Schema migrations** | Changing tables without losing data | `_add_col(...)`, table rebuilds in `shop_db.py` |
| **ETL** (extract, transform, load) | Read messy input, clean it, load it | Word/Excel inventory → CSV → setup file → database (Transfer page) |
| **Data cleaning** | Normalizing text, units, numbers | `norm()`: `'lit'` → liter, `'1/2"'` → `1/2 inch`, peso signs, commas |
| **Fuzzy matching** | Edit distance, token similarity, rare-word weighting (like TF-IDF) | Matching OCR text to items (`ocr_engine.py`, `rapidfuzz`) |
| **Data quality checks** | Validation, "please check" flags, totals that must add up | Qty × price = amount checks, receipt totals |
| **Exports + reporting** | CSV for Excel, ZIP archives, aggregates | Day download, archive, restock list, dashboards |
| **Backups + archiving** | Copies before risky actions, moving old data out | `backups/`, Archive & free space |

### 3. Backend development
| Topic | Where it is used |
|---|---|
| **HTTP and REST APIs** (FastAPI): routes, JSON, status codes, file uploads | `backend/server.py`, `backend/shop_api.py` |
| **Authentication**: password hashing (PBKDF2), session tokens, login rate limits | `server.py` |
| **Authorization**: desktop-only pages, owner vs sales person | `require_desk`, `require_local` |
| **Concurrency**: a separate OCR **process**, job queues, background threads | `OcrService`, `OcrWorker` |
| **Image processing**: Pillow and OpenCV (rotate, crop, thumbnails) | `ocr_engine.py` |
| **OCR / machine-learning inference**: PaddleOCR text detection + recognition, CPU speed-ups | `ocr_engine.py` |
| **Networking**: IP addresses, ports, UDP broadcast discovery, firewalls, hotspot | `server.py`, [docs/NETWORKING.md](docs/NETWORKING.md) |
| **Logging and error handling** | `shop_server.log`, friendly error messages |
| **Testing**: API tests (FastAPI TestClient), browser tests (Playwright) | — |

### 4. Frontend and desktop
- **React** (components, state, effects, routing), **Vite** (build), **Tailwind CSS** (styling).
- **pywebview** (a native window around the web pages) and **PyInstaller** (one folder with an `.exe`).

### 5. Soft skills that matter just as much
- Talking to the shop owner, writing down how they *really* work, and changing the system after real use.
- Explaining errors in plain words to non-technical users.

---

## How the system is built

```
 ┌─────────────┐   Wi-Fi (HTTP, port 8000)   ┌──────────────────────────── Laptop ─────────────────────────────┐
 │ Phone app   │ ──────────────────────────► │  FastAPI server (Python)                                         │
 │ (Flutter)   │   photo, sign-in, updates   │   ├─ shop API (sales, supply, cash flow, planner, transfer...)   │
 └─────────────┘                             │   ├─ OCR process (PaddleOCR) ── job queue ──► reads receipts      │
                                             │   └─ SQLite database + photo folders (AppData)                   │
 ┌──────────────────────────┐   HTTP (local) │                                                                  │
 │ Desktop window           │ ◄────────────► │  serves the React pages (built with Vite)                        │
 │ (pywebview + WebView2)   │                └──────────────────────────────────────────────────────────────────┘
 └──────────────────────────┘
```

| Folder | Contents |
|---|---|
| `backend/server.py` | The web server: sign-in, receipts from phones, the OCR service, networking helpers |
| `backend/shop_api.py` | The shop's API: sales, supply, orders, cash flow, planner, calendar, transfer |
| `backend/shop_db.py` | Database tables and migrations |
| `backend/ocr_engine.py` | Receipt reading: OCR, table reading, matching to items |
| `backend/app_launcher.py` | The desktop program: starts the server, opens the window |
| `backend/build_exe.py` | Makes the `.exe` (PyInstaller) |
| `src/` | The desktop pages (React) |
| `docs/` | Guides (networking, web → desktop) |

---

## From web app to desktop program

Short version (full guide: [docs/WEB_TO_DESKTOP.md](docs/WEB_TO_DESKTOP.md)):

1. **Build the pages:** `npm run build` turns the React code into plain HTML/CSS/JS files in `dist/`.
2. **Let the Python server serve them:** FastAPI returns `dist/index.html` and `dist/assets/*`, so one program
   is both the website and the API (`http://127.0.0.1:8000`).
3. **Open a native window instead of a browser:** `pywebview` shows that address in a real window (on Windows it
   uses Microsoft Edge WebView2). Closing the window stops the server.
4. **Package everything:** `PyInstaller` bundles Python, the libraries, the OCR models and the built pages into
   `release/TresNinosShop/` with `TresNinosShop.exe`. No Python or Node.js is needed on the shop's laptop.
5. **Desktop details a browser does not need:** only one copy may run, the data lives in
   `%LOCALAPPDATA%\TresNinosShop` (not next to the `.exe`), downloads open a *Save as* box through a JavaScript ↔
   Python bridge, the laptop is kept awake while the program is open, and the firewall is configured on first start.

---

## How to make money with a system like this

A system built around how a *specific* kind of shop works is something small businesses will pay for, because
generic software rarely fits them. Ways to earn from it:

| Model | What the client pays for | Notes |
|---|---|---|
| **Setup fee** (one-time) | Installing on their laptop, importing their inventory, creating staff logins, training | Price by the hours it takes plus the value to them (time saved counting stock, fewer lost sales). |
| **Monthly subscription / support** | Updates, fixes, backups, help by phone or chat | Recurring income; keep it simple (one price per shop). |
| **Customization** | A feature only they need (their invoice layout, a new report, a delivery rule) | Quote per feature; reuse it for the next client. |
| **Data services** | Turning their paper / Word / Excel inventory into clean data | Exactly the ETL work described above. |
| **Hardware bundle** | A laptop, phones, a router set up and tested | Add a margin; you also solve their network problems. |
| **Template for an industry** | The same system for many hardware stores, gravel and sand traders, sari-sari stores | Build once, sell many times; each new client is mostly setup. |

**How to find clients:** start with shops you know, show a demo on your own laptop with sample data, offer a
trial month, and ask happy clients to refer others (owners of similar shops know each other).

**Before selling, take care of:**
- **Data privacy:** you store employees' and customers' information. In the Philippines this falls under the
  **Data Privacy Act of 2012 (RA 10173)**: collect only what is needed, protect it, and explain to the client
  how it is stored.
- **Receipts and taxes:** this system *reads* receipts; it does not issue official ones. If you build a version
  that prints official receipts or invoices, check the **BIR's** rules for POS and computerized accounting
  systems first.
- **Backups and support:** a shop that loses its records loses money. Build backups in, and only promise the
  support hours you can really give.
- **A written agreement:** what is included, what costs extra, who owns the data (the client does).

*This is general information, not legal or financial advice.*

---

## Run it yourself

Requirements: Windows 10/11, Python 3.11, Node.js 20+.

```bash
# 1. the pages
npm install
npm run dev                 # http://localhost:5173 (development)

# 2. the server (another terminal)
cd backend
pip install -r requirements.txt
pip install paddlepaddle paddleocr   # the OCR engine (CPU build)
python server.py serve      # http://127.0.0.1:8000

# 3. the desktop program (.exe)
build_exe.bat               # or: npm run build && python backend/build_exe.py
```

The first start asks you to choose a **system password** and your own account. Phones sign in with logins you
create in Cash Flow → Employee → Phone login.

---

## Keeping secrets out of GitHub

The repository contains **code only**. Everything personal stays on the shop's laptop:

| Kept out (see `.gitignore`) | Why |
|---|---|
| `backend/inventory.db*`, `uploads/`, `avatars/`, `backups/`, `transfer_in/` | The shop's real records and photos |
| `starter/`, `mock_data/`, `*.zip`, `*.xlsx` | Setup files with staff logins (password hashes) and the real inventory |
| `private/` | Personal notes, e.g. the ERD script |
| `net_info.txt`, `*.log` | Network details and logs |
| `release/`, `.buildenv/`, `dist/`, `node_modules/`, `backend/build/` | Build output (large, rebuilt any time) |

Also:
- Passwords are never stored in plain text: only **PBKDF2-SHA256** hashes with a salt.
- No API keys or passwords are written in the code; Wi-Fi passwords and phone passwords are never committed.
- **Before every push**, look at the list of changed files in GitHub Desktop (or `git status`). If a database,
  zip or photo appears, stop and add it to `.gitignore`.
- If a secret is ever pushed by mistake, **change that password first**, then remove the file from the history.

---

## More documents

- [docs/NETWORKING.md](docs/NETWORKING.md): how the phones reach the laptop; extenders, client isolation,
  Mobile Hotspot, ICS; a beginner's glossary.
- [docs/WEB_TO_DESKTOP.md](docs/WEB_TO_DESKTOP.md): turning the React + FastAPI web app into a Windows program.
- [BUILD_EXE.md](BUILD_EXE.md): building `TresNinosShop.exe` step by step.
