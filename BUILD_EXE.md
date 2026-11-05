# Making TresNinosShop.exe

The result is a **folder** `release\TresNinosShop\` with `TresNinosShop.exe` inside. Double-click it: the shop server
starts in the background and the desktop app opens in its own window (no terminal, no `npm run dev`).
Closing the window stops the server. Double-clicking again while it is open just brings the window to the front
(only one copy runs at a time). Give the **whole folder** to someone (zip it), not only the .exe.

**Standalone:** Node.js and Python are needed only on the computer that **builds** it (yours). The folder it makes
runs on any Windows 10/11 laptop with nothing installed.

## Build it (on your laptop, about 20–30 minutes the first time)

You need what you already use: Node.js (for `npm`) and the Python that runs `server.py` (it has PaddleOCR), plus
internet the first time.

The build does **not** use your everyday Python directly: that one also has PyTorch, TensorFlow, Jupyter, Qt... and
the program would carry all of them (gigabytes). The first build makes a clean Python in `Tress\.buildenv` with only
what the shop needs (FastAPI, Pillow, PaddleOCR at the same version as yours, CPU PaddlePaddle) and builds from that.
Later builds reuse it. (`--use-this-python` skips it, not recommended.)

1. Open the `Tress` folder in File Explorer.
2. Double-click **`build_exe.bat`**. It does three things by itself:
   - `npm run build` (makes the desktop pages),
   - `pip install pyinstaller pywebview`,
   - `python backend\build_exe.py` (packs everything, including PaddleOCR, into `release\TresNinosShop\`).
3. When it says **Finished**, open `release\TresNinosShop` and double-click `TresNinosShop.exe`.

If you prefer a terminal (same thing):
```
cd C:\Users\David\Desktop\TN\program\Tress
npm run build
python -m pip install pyinstaller pywebview
cd backend
python build_exe.py
```

## Where your data is (the program folder never has it)

The .exe keeps the shop's records **outside** the program folder, in
`C:\Users\<you>\AppData\Local\TresNinosShop` (`inventory.db`, `uploads`, `avatars`, `product_images`, `shop_server.log`).
So `release\TresNinosShop` can be zipped and given to anyone: it carries **no** sales, passwords or photos. Each laptop
that opens it starts empty and sets its own system password.

- Older versions kept the data next to the .exe. The first time the new version opens, it **moves** that data to the
  AppData folder by itself. Open the new TresNinosShop once on your laptop before you zip the folder.
- **Updating the program:** close TresNinosShop, run `build_exe.bat`, open it again. The data is not touched.
- **Moving the shop to a NEW laptop (with the records):** copy the whole `AppData\Local\TresNinosShop` folder to the same
  place on the new laptop (type `%LOCALAPPDATA%` in the File Explorer address bar to find it), plus the program folder.
- **Backups:** Calendar → Archive (a ZIP with the full database). Keep a copy at home.
- `build_exe.bat --with-my-data` copies `backend\inventory.db` + photos into the AppData folder, only if it has no
  database yet. `build_exe.bat --share` makes the same clean program as `release\TresNinosShop_share`.

## On the other computer

- Windows 10/11, 64-bit. Nothing else to install (Python, Node and PaddleOCR are inside the folder).
- Copy the folder anywhere it can write (Desktop or Documents, **not** `C:\Program Files`), then double-click the .exe.
- The first time, Windows SmartScreen may say "Windows protected your PC" (the program is not signed):
  **More info → Run anyway**.
- The first time, Windows Firewall asks about network access: tick **Private networks** only (see the security guide).
- **OCR models:** the first receipt read downloads the PaddleOCR model files (internet needed once; they are saved in
  `C:\Users\<name>\.paddlex`). For a PC without internet, copy your own `C:\Users\David\.paddlex` folder to the same place
  on the other PC.
- **CPU OCR:** the program uses the CPU version of PaddlePaddle, so it runs on any laptop (with or without an NVIDIA
  card). A receipt takes a few seconds longer than on a GPU.
- The phones find the new PC by themselves on the same Wi-Fi, as before.

## Jobs from the command line still work

Open a terminal in the program folder:
```
TresNinosShop.exe set-system-password     (forgot it)
TresNinosShop.exe list-users
TresNinosShop.exe passwd <username>
```
(Each one opens its own small console window for the questions and closes when you press Enter.)

## If it does not start

- Look at `shop_server.log` in `%LOCALAPPDATA%\TresNinosShop`: everything the server prints goes there.
- "Another shop server is already running": a `python server.py serve` terminal is still open. Close it; use only
  TresNinosShop (it has its own database next to the .exe).
- Build errors about a missing package: install it with `pip install <name>` in the same Python, then build again
  (PaddleOCR's own packaging guide: https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/others/packaging.html).
- Windows Defender occasionally flags new unsigned PyInstaller programs. Allow it in Windows Security → Protection
  history, or restore it from quarantine.

## Updating later

Close TresNinosShop, run `build_exe.bat`, open it again. Your data (in AppData) is not touched.

## The icon
`backend\app.ico` (made from `logo.jpg`) is the program's icon: the .exe, its shortcut and the taskbar. To change it,
replace app.ico (256×256 .ico) and build again.
