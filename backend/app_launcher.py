r"""
app_launcher.py  =  the program inside TresNinosShop.exe (see BUILD_EXE.md).

Double-click the .exe:
  1. starts the shop server (the same as  python server.py serve) in the background,
  2. opens the desktop app in its own window (pywebview), or in the web browser if pywebview is missing,
  3. closing the window stops the server. While it is open the laptop does not fall asleep by itself (keep_awake).
  4. the first start on a laptop checks that phones can connect and, if Windows would block them, asks Windows once
     to allow them (first_start_phone_check; the same as the desktop's "Fix it" button).
Download buttons (Calendar day, archive, setup file, restock list) open a "Save as" box here (class SaveFiles):
the app window does not show downloads like a web browser does.
Only ONE copy runs at a time: double-clicking again just brings the open window to the front (two copies used to
fight over the server, and closing one left the other saying "server offline").

The data (inventory.db, uploads, avatars, product_images) lives in  C:\Users\<name>\AppData\Local\TresNinosShop ,
NOT in the program folder: the program folder can be zipped / copied / rebuilt without carrying or touching any records.
(Older versions kept it next to the .exe; it is moved there automatically the first time.)
A log of what the server printed is kept in shop_server.log in that data folder.

Command-line jobs still work through the .exe, from a terminal in its folder:
    TresNinosShop.exe set-system-password
    TresNinosShop.exe list-users
    TresNinosShop.exe passwd <username>
"""

import logging
import multiprocessing
import sys
import threading
import time
import urllib.request

PORT = 8000
URL = f"http://127.0.0.1:{PORT}/"
TITLE = "Tres Ninos Hardware"


def keep_a_log(folder):
    """A windowed .exe has no console: print() would crash. Send everything to shop_server.log instead."""
    if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
        log = open(folder / "shop_server.log", "a", encoding="utf-8", buffering=1)
        log.write(f"\n===== started {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        sys.stdout = sys.stderr = log


def open_console():
    """The .exe is a window program (no black terminal). For the command-line jobs it opens its own console window,
    so questions like 'New system password:' can be answered."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    import ctypes
    ctypes.windll.kernel32.AllocConsole()
    sys.stdin = open("CONIN$", "r")
    sys.stdout = sys.stderr = open("CONOUT$", "w", buffering=1)
    return True


_MUTEX = None


def already_open():
    """True when another TresNinosShop window is open (Windows named mutex). Brings that window to the front."""
    global _MUTEX
    if sys.platform != "win32":
        return False
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.windll.user32
    k32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k32.CreateMutexW(None, False, "Local\\TresNinosShopSingleInstance")
    if ctypes.get_last_error() != 183:                 # 183 = ERROR_ALREADY_EXISTS
        return False
    for _ in range(20):                                # its window may still be opening
        h = u32.FindWindowW(None, TITLE)
        if h:
            u32.ShowWindow(h, 9)                       # SW_RESTORE
            u32.SetForegroundWindow(h)
            break
        time.sleep(0.5)
    return True


def message(text):
    print(text)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x30)


def server_running():
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=1.5) as r:
            return b"tres-ninos-shop" in r.read()
    except Exception:
        return False


def start_server():
    import uvicorn
    import server
    server.SERVE_PORT = PORT
    server.SERVICE.configure("auto", "medium", server.E.DEFAULT_SIDE)
    config = uvicorn.Config(server.app, host="0.0.0.0", port=PORT, log_level="info", log_config=None)
    srv = uvicorn.Server(config)
    srv.install_signal_handlers = lambda: None          # runs in a thread: Ctrl+C handling stays with the window
    srv.thread = threading.Thread(target=srv.run, daemon=True, name="server")
    srv.thread.start()
    return srv


def keep_alive(box):
    """If the server ever stops by itself while the window is open, start it again (the window reconnects)."""
    while not box["closing"]:
        time.sleep(5)
        srv = box["srv"]
        if not box["closing"] and srv and not srv.thread.is_alive() and not server_running():
            logging.warning("the server stopped by itself - starting it again")
            box["srv"] = start_server()


def keep_awake(on):
    """While the program is open the laptop does not fall asleep by itself (the screen may still turn off), so the
    phones can always send their receipts. Closing the program gives the normal sleep back.
    (Closing the lid still does what Windows is set to do.)"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def first_start_phone_check():
    """The first time this .exe runs on a laptop (or from another folder): if Windows would block the phones (Wi-Fi set
    to Public, or no firewall permission for this program yet), ask Windows once to allow them - the same as the
    "Fix it" button on the desktop. Windows shows its "Do you want to allow..." question: press Yes."""
    if sys.platform != "win32":
        return
    import hashlib
    import ocr_engine
    import server
    mark = ocr_engine.APP_DIR / f"phones_allowed_{hashlib.md5(sys.executable.lower().encode()).hexdigest()[:10]}.txt"
    if mark.exists():
        return
    try:
        fw = server.firewall_state(fresh=True)
        problems = server.phone_problems(server.windows_networks(fresh=True), fw)
        if not problems and fw is not None:
            mark.write_text(f"{time.strftime('%Y-%m-%d %H:%M')} phones could already connect\n{sys.executable}\n")
            return
        logging.info("phones cannot connect yet (%s): asking Windows to allow them",
                     problems[0]["text"] if problems else "firewall not readable")
        left = server.allow_phones()
        mark.write_text(f"{time.strftime('%Y-%m-%d %H:%M')} allowed; left: {[p['text'] for p in left]}\n{sys.executable}\n")
        logging.info("phone access: %s", "OK" if not left else left[0]["text"])
    except PermissionError:
        mark.write_text(f"{time.strftime('%Y-%m-%d %H:%M')} Windows question answered No: use Fix it on the desktop\n")
        logging.warning("phone access: the Windows question was answered No (the desktop shows Fix it)")
    except Exception as e:                               # not answered / PowerShell missing: try again next start
        logging.warning("phone access check failed: %s", e)


def open_window():
    try:
        import webview                                  # pip install pywebview
    except ImportError:
        import webbrowser
        webbrowser.open(URL)
        print("pywebview is not installed: opened the web browser instead. Close this program to stop the server.")
        while True:
            time.sleep(3600)
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True       # any plain download link still lands in Downloads
    except Exception:
        pass
    win = webview.create_window(TITLE, URL, width=1400, height=900, min_size=(1100, 700), js_api=SaveFiles(),
                                text_select=True)
    SaveFiles._window = win
    keep_awake(True)
    try:
        webview.start()                                  # returns when the window is closed
    finally:
        keep_awake(False)


def downloads_folder():
    """The Windows Downloads folder (it can be moved to another drive, so ask Windows first)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            import uuid
            guid = uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}")          # FOLDERID_Downloads
            buf = ctypes.c_wchar_p()
            g = (ctypes.c_byte * 16).from_buffer_copy(guid.bytes_le)
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(buf)) == 0:
                path = buf.value
                ctypes.windll.ole32.CoTaskMemFree(buf)
                return path
        except Exception:
            pass
    from pathlib import Path
    d = Path.home() / "Downloads"
    return str(d if d.is_dir() else Path.home())


class SaveFiles:
    """Called from the page as  window.pywebview.api.save_download(link)  (src/lib/api.js -> saveDownload).
    The app window (WebView2) does not show downloads the way a web browser does, so every Download button
    asks WHERE to save the file (a normal Windows "Save as" box, opened in Downloads), saves it there
    and opens that folder with the file selected."""
    _window = None                                       # set by open_window (starts with _ : not sent to the page)

    def save_download(self, link):
        import os
        import shutil
        import tempfile
        from urllib.error import HTTPError
        from urllib.parse import urlsplit
        part = urlsplit(str(link))
        if not part.path.startswith("/api/"):
            return {"ok": False, "error": "Not a shop download."}
        url = URL.rstrip("/") + part.path + (f"?{part.query}" if part.query else "")
        tmp = None
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                name = r.headers.get_filename() or os.path.basename(part.path) or "download"
                fd, tmp = tempfile.mkstemp(prefix="shop_dl_")
                with os.fdopen(fd, "wb") as f:
                    shutil.copyfileobj(r, f)
        except HTTPError as e:
            msg = f"Error {e.code}"
            try:
                import json
                d = json.loads(e.read().decode("utf-8", "replace")).get("detail")
                msg = d if isinstance(d, str) else (d or {}).get("message", msg)
            except Exception:
                pass
            return {"ok": False, "error": msg}
        except Exception as e:
            return {"ok": False, "error": f"Could not download: {e}"}
        try:
            import webview
            kind = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
            ext = os.path.splitext(name)[1].lstrip(".")
            types = (f"{ext.upper()} file (*.{ext})", "All files (*.*)") if ext else ()
            try:
                picked = self._window.create_file_dialog(kind, directory=downloads_folder(), save_filename=name,
                                                        file_types=types)
            except Exception:
                picked = self._window.create_file_dialog(kind, directory=downloads_folder(), save_filename=name)
            if isinstance(picked, (list, tuple)):
                picked = picked[0] if picked else None
            if not picked:
                return {"ok": False, "cancelled": True}
            dest = str(picked)
            if ext and not dest.lower().endswith("." + ext.lower()):
                dest += "." + ext
            shutil.move(tmp, dest)
            tmp = None
            if sys.platform == "win32":
                import subprocess
                subprocess.Popen(["explorer", "/select,", os.path.normpath(dest)])
            return {"ok": True, "path": dest}
        except Exception as e:
            return {"ok": False, "error": f"Could not save the file: {e}"}
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)


def main():
    if len(sys.argv) > 1:                                # TresNinosShop.exe set-system-password  (and the other jobs)
        console = open_console()
        import server
        try:
            server.main(sys.argv[1:])
        except SystemExit as e:
            if e.code not in (None, 0):
                print(e.code)
        if console:
            input("\nPress Enter to close this window.")
        return
    if already_open():                                   # second double-click: the open window comes to the front
        return
    import ocr_engine
    try:
        moved = ocr_engine.move_old_data()               # old versions: data next to the .exe -> the data folder
    except OSError as e:
        moved = f"could not move the old data yet ({e}); it is tried again next time"
        if ocr_engine.EXE_DIR and (ocr_engine.EXE_DIR / "inventory.db").exists():
            ocr_engine.APP_DIR = ocr_engine.EXE_DIR       # this time keep using it where it is (nothing looks empty)
            ocr_engine.DB_PATH = ocr_engine.EXE_DIR / "inventory.db"
    keep_a_log(ocr_engine.APP_DIR)
    if moved:
        print(f"[data] {moved}")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="[%(asctime)s] %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    if server_running():
        message("Another shop server is already running on this laptop (probably a terminal with\n"
                "python server.py serve). Close it first, then open TresNinosShop again.\n\n"
                "Use only TresNinosShop from now on: it keeps its own database in AppData\\Local\\TresNinosShop.")
        return
    box = {"srv": start_server(), "closing": False}
    for _ in range(240):                                 # the first start can take a while
        if server_running():
            break
        if not box["srv"].thread.is_alive():
            break
        time.sleep(0.5)
    if not server_running():
        message(f"The shop server could not start. Details are in {ocr_engine.APP_DIR / 'shop_server.log'}\n"
                "If another program uses port 8000, close it and try again.")
        return
    threading.Thread(target=keep_alive, args=(box,), daemon=True, name="keep-alive").start()
    threading.Thread(target=first_start_phone_check, daemon=True, name="phone-check").start()
    try:
        open_window()
    finally:
        box["closing"] = True
        box["srv"].should_exit = True
        time.sleep(1.5)                                  # let the server stop the OCR process cleanly


if __name__ == "__main__":
    multiprocessing.freeze_support()                     # REQUIRED: the OCR runs in a second process of the .exe
    main()
