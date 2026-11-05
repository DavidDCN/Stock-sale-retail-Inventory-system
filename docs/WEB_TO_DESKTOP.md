# From a web app to a desktop program

How the React + FastAPI web app became `TresNinosShop.exe`, a program the shop owner opens with a double-click.

## Why a desktop program at all

During development the system ran as **two programs in two terminals**: `npm run dev` (the pages, port 5173) and
`python server.py serve` (the API, port 8000), opened in a browser. A shop owner should not need terminals,
Node.js or Python. The goal was: **one icon → one window**, with the data safe and the phones able to connect.

## The five steps

### 1. Build the pages into plain files
```bash
npm run build          # tsc -b && vite build
```
Vite compiles the React code into `dist/index.html` and `dist/assets/*.js / *.css`. These are ordinary files
that any web server can send. No Node.js is needed after this.

### 2. Let the Python server serve those files
The FastAPI server mounts `dist/assets` and returns `dist/index.html` for every page address (`/sales`,
`/supply`, …), so React's router still works. The pages call the API on the **same address**, so there is no
"which server?" setting and no cross-origin problem:

```python
WEB_DIR = ...  # dist/ in development, the bundled "web" folder inside the .exe
app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"))
@app.get("/{path:path}")
def page(path): return FileResponse(WEB_DIR / "index.html")
```

### 3. Show it in a native window (pywebview)
`app_launcher.py` starts the server in a background thread, waits until `/api/ping` answers, then:

```python
import webview
win = webview.create_window("Tres Ninos Hardware", "http://127.0.0.1:8000/",
                            width=1400, height=900, js_api=SaveFiles())
webview.start()          # returns when the window is closed -> the server is stopped
```

On Windows, pywebview uses **Microsoft Edge WebView2** (already part of Windows 10/11), so the window looks and
behaves like the browser version, but has its own icon and title, with no address bar.

### 4. Package it (PyInstaller)
`build_exe.py` runs PyInstaller from a **clean virtual environment** (`.buildenv`) that holds only what the shop
needs. Your everyday Python would drag in PyTorch, Jupyter and more (gigabytes).

```
pyinstaller app_launcher.py --name TresNinosShop --windowed --icon app.ico
            --add-data dist;web                       # the built pages
            --collect-data paddlex --collect-binaries paddle   # the OCR engine
```

The result is a **folder** `release/TresNinosShop/` with `TresNinosShop.exe` and an `_internal/` folder. Give
the whole folder (zipped) to a client; nothing else has to be installed.

### 5. The details a browser never needed
| Problem | Solution |
|---|---|
| The OCR runs in a second process; a frozen `.exe` would start itself again forever | `multiprocessing.freeze_support()` at the top of the launcher |
| Double-clicking twice started two servers fighting over port 8000 | A Windows **named mutex**: the second copy only brings the open window to the front |
| Data next to the `.exe` would be lost when the folder is replaced | Data lives in `%LOCALAPPDATA%\TresNinosShop` (database, photos, log) |
| A windowed `.exe` has no console: `print()` crashes | Everything is written to `shop_server.log` |
| WebView2 does not show downloads like a browser | A **JavaScript ↔ Python bridge** (`js_api`): the page calls `window.pywebview.api.save_download(link)`; Python opens a *Save as* box, saves the file and opens the folder |
| The laptop fell asleep, so phones could not send photos | While the window is open, Windows is told to stay awake (`SetThreadExecutionState`) |
| Windows Firewall blocks phones on a new laptop | First start: one Windows *Yes* creates the firewall rules and sets the Wi-Fi to Private |
| A new version must not keep using old pages | Server and pages carry the same version number; a mismatch shows a banner |

## Alternatives you could choose instead
| Tool | Good for | Why not here |
|---|---|---|
| **Electron** | JavaScript-only apps | Would still need Python for the OCR; much larger download |
| **Tauri** | Small apps with a Rust core | Would still need Python for the OCR; more languages to learn |
| **PWA** (installable website) | Simple apps | Cannot run the OCR or manage the firewall / hotspot |
| **pywebview + PyInstaller** | A Python backend with a web front end | ✔ what we used: one language for the backend, the same pages as the web version |
