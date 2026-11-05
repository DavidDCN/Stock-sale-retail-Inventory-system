"""
build_exe.py  =  makes  Tress/release/TresNinosShop/TresNinosShop.exe   (Windows; see BUILD_EXE.md)

    python build_exe.py                  makes / UPDATES  release/TresNinosShop . It never contains shop data: the .exe
                                         keeps its records in  C:/Users/<name>/AppData/Local/TresNinosShop , so the
                                         folder can be zipped and given to anyone. Close TresNinosShop first.
    python build_exe.py --with-my-data   copies backend/inventory.db + photos into that AppData folder, only if it has
                                         no database yet (never overwrites)
    python build_exe.py --share          the same clean program, as  release/TresNinosShop_share
    python build_exe.py --nvidia         also packs the NVIDIA GPU libraries (only if the OCR uses a GPU)

It builds inside a CLEAN Python (the folder Tress/.buildenv, made the first time, internet needed) that holds ONLY what
the shop needs: FastAPI, Pillow, PaddleOCR (same versions as yours) ... Built from your everyday Python instead, the
program would also carry PyTorch, TensorFlow, Jupyter, Qt... (gigabytes). --use-this-python skips the clean Python.

Before running it:  npm run build  in the Tress folder (makes Tress/dist, the desktop pages),
and  pip install pyinstaller pywebview  in the same Python that runs the server.

The PaddleOCR part follows PaddleOCR's own packaging guide: --collect-data paddlex, --collect-binaries paddle and
--copy-metadata for every PaddleX dependency that is installed.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from importlib.metadata import distributions
from pathlib import Path

HERE = Path(__file__).resolve().parent          # Tress/backend
ROOT = HERE.parent                              # Tress
WEB = ROOT / "dist"                             # npm run build
OUT = ROOT / "release"
NAME = "TresNinosShop"
DATA = ["inventory.db", "inventory.db-wal", "inventory.db-shm", "uploads", "avatars", "product_images", "payment_photos", "opened_data", "transfer_in", "ai",
        "shop_server.log"]
STAGE = ROOT / "release_build"                  # PyInstaller writes here first; then the program files are copied over
VENV = ROOT / ".buildenv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
# never needed by the shop; if they are installed they would only make the program huge
EXCLUDE = ["tensorflow", "keras", "tf_keras", "torchvision", "torchaudio", "PyQt5", "PyQt6", "PySide2", "PySide6",
           "IPython", "jupyter", "jupyter_client", "jupyter_core", "notebook", "ipykernel", "nbformat",
           "pytest", "debugpy"]


def installed_version(name):
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def pip(args):
    print(" ".join(["pip"] + args))
    return subprocess.run([str(VENV_PY), "-m", "pip", *args]).returncode == 0


def run_in_clean_python():
    """Make Tress/.buildenv (once), install only the shop's packages there, then run this script again inside it."""
    if not VENV_PY.exists():
        print(f"Making a clean Python for the build in {VENV} (only the first time)...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        pip(["install", "--upgrade", "pip"])
    pins = []
    for name in ("paddleocr", "paddlex"):              # the same OCR versions that already work on this laptop
        v = installed_version(name)
        pins.append(f"{name}=={v}" if v else name)
    paddle = installed_version("paddlepaddle") or installed_version("paddlepaddle-gpu")
    base = ["fastapi", "uvicorn", "python-multipart", "pillow", "numpy", "rapidfuzz", "pyinstaller", "pywebview"]
    if not pip(["install", *base, *pins]):
        sys.exit("Installing the packages into the clean Python failed (see above). Is the internet on?")
    # the CPU build of PaddlePaddle (runs on every PC); same version as yours when PyPI has it
    if not (paddle and pip(["install", f"paddlepaddle=={paddle.split('+')[0]}"])) and not pip(["install", "paddlepaddle"]):
        sys.exit("Installing PaddlePaddle into the clean Python failed (see above).")
    print("Clean Python ready. Building...")
    code = subprocess.run([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:], "--inside-clean-python"]).returncode
    sys.exit(code)


def bundle_ocr_models(target):
    """Copy the reading model files this laptop already downloaded (C:/Users/<you>/.paddlex/official_models) into
    <program>/ocr_models, so another laptop reads receipts at once: no internet, no first-start download."""
    cache = Path(os.environ.get("PADDLE_PDX_CACHE_HOME") or (Path.home() / ".paddlex")) / "official_models"
    found = sorted(d for d in cache.iterdir() if d.is_dir() and d.name.endswith(("_det", "_rec"))) if cache.is_dir() else []
    if not found:
        print(f"NOTE: no OCR model files found in {cache}. Open TresNinosShop once and read one receipt on THIS laptop "
              "(it downloads them), then build again so they are packed in.")
        return
    dest = target / "ocr_models"
    shutil.rmtree(dest, ignore_errors=True)
    total = 0
    for d in found:
        shutil.copytree(d, dest / d.name)
        total += sum(f.stat().st_size for f in (dest / d.name).rglob("*") if f.is_file())
    print(f"Packed the OCR models ({', '.join(d.name for d in found)}; {total / 1048576:.0f} MB): "
          "receipts can be read on any laptop without internet.")


def program_running():
    """Is TresNinosShop.exe running right now? (its files cannot be replaced while it runs)"""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {NAME}.exe", "/NH"], capture_output=True, text=True, timeout=20)
        return f"{NAME}.exe".lower() in out.stdout.lower()
    except Exception:
        return False


def paddlex_metadata():
    """PaddleX checks its optional dependencies through package metadata at run time, so it must be copied in."""
    try:
        import paddlex.utils.deps as deps
    except ImportError:
        sys.exit("PaddleOCR / PaddleX is not installed in this Python. Run this with the Python that runs the server.")
    specs = getattr(deps, "DEP_SPECS", None) or getattr(deps, "BASE_DEP_SPECS", {})
    norm = lambda n: n.lower().replace("_", "-")
    wanted = {norm(k) for k in specs}
    names = {d.metadata["Name"] for d in distributions() if d.metadata["Name"]}
    return sorted(n for n in names if norm(n) in wanted)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-my-data", action="store_true", help="copy this shop's database and photos into the program")
    ap.add_argument("--share", action="store_true", help="make a clean copy without any data, to give to another shop")
    ap.add_argument("--nvidia", action="store_true", help="also pack CUDA / cuDNN (GPU OCR)")
    ap.add_argument("--use-this-python", action="store_true", help="build from this Python instead of the clean one")
    ap.add_argument("--inside-clean-python", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if not (a.use_this_python or a.inside_clean_python):
        if not (WEB / "index.html").exists():
            sys.exit(f"{WEB} is missing. Run  npm run build  in {ROOT} first.")
        run_in_clean_python()

    if not (WEB / "index.html").exists():
        sys.exit(f"{WEB} is missing. Run  npm run build  in {ROOT} first.")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is missing:  pip install pyinstaller pywebview")

    cmd = [sys.executable, "-m", "PyInstaller", str(HERE / "app_launcher.py"),
           "--name", NAME, "--windowed", "--noconfirm", "--clean",
           "--distpath", str(STAGE), "--workpath", str(HERE / "build"), "--specpath", str(HERE / "build"),
           "--paths", str(HERE),
           "--add-data", f"{WEB}{os.pathsep}web",
           "--hidden-import", "server", "--hidden-import", "shop_api", "--hidden-import", "shop_db",
           "--hidden-import", "ocr_engine",
           "--collect-submodules", "uvicorn",
           "--collect-data", "paddlex", "--collect-binaries", "paddle"]
    for m in paddlex_metadata():
        cmd += ["--copy-metadata", m]
    for m in EXCLUDE:
        cmd += ["--exclude-module", m]
    if a.nvidia:
        cmd += ["--collect-binaries", "nvidia"]
    if (HERE / "app.ico").exists():
        cmd += ["--icon", str(HERE / "app.ico")]

    if program_running():
        sys.exit("TresNinosShop is open. Close it first (check the taskbar), then run the build again.")
    print("Running PyInstaller (this takes several minutes)...")
    subprocess.run(cmd, check=True)

    built = STAGE / NAME
    for name in DATA:                                  # never ship anyone's data from the build folder
        p = built / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    if a.share:                                        # a clean copy for another shop
        target = OUT / f"{NAME}_share"
        shutil.rmtree(target, ignore_errors=True)
        OUT.mkdir(parents=True, exist_ok=True)
        shutil.copytree(built, target)
        shutil.rmtree(STAGE, ignore_errors=True)
        bundle_ocr_models(target)
        print(f"\nDone:  {target}\nZip this WHOLE folder and give it away. It has no shop data: the other shop sets its "
              "own system password the first time.")
        return

    target = OUT / NAME
    target.mkdir(parents=True, exist_ok=True)
    has_data = (target / "inventory.db").exists()        # an OLD build kept data here: the new .exe moves it on start
    # replace the program files: the .exe and its _internal folder MUST come from the same build (a new _internal
    # with an old .exe gives "Failed to start embedded python interpreter"). Windows / the antivirus can hold the
    # fresh files for a few seconds, so try a few times.
    for attempt in range(6):
        try:
            if (target / "_internal").exists():
                shutil.rmtree(target / "_internal")
            shutil.copytree(built / "_internal", target / "_internal")
            shutil.copy2(built / f"{NAME}.exe", target / f"{NAME}.exe")
            break
        except OSError as e:
            if attempt == 5:
                sys.exit(f"Windows kept the program files locked ({e}).\nThe program folder is INCOMPLETE now: close "
                         "TresNinosShop (check the taskbar), wait a minute, and run build_exe.bat again. "
                         "Your data was not touched.")
            print("Files are busy (antivirus or the program is open); trying again in 5 seconds...")
            time.sleep(5)
    shutil.rmtree(STAGE, ignore_errors=True)
    bundle_ocr_models(target)

    data_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / NAME
    if a.with_my_data:
        if (data_dir / "inventory.db").exists() or has_data:
            print("The program already has its own database (the one you have been using): kept, nothing copied.")
        else:
            data_dir.mkdir(parents=True, exist_ok=True)
            for name in ("inventory.db", "uploads", "avatars", "product_images", "payment_photos"):
                src = HERE / name
                if src.is_dir():
                    shutil.copytree(src, data_dir / name, dirs_exist_ok=True)
                elif src.exists():
                    shutil.copy2(src, data_dir / name)
            print(f"Copied the database and photos from backend/ to {data_dir}")
    print(f"\nDone:  {target / (NAME + '.exe')}")
    if has_data:
        print("This folder still has data from the old version: the first time you open the new TresNinosShop it moves "
              f"it to {data_dir}. Open it once BEFORE you zip or copy the folder.")
    else:
        print(f"The folder has no shop data (that lives in {data_dir}): you can zip it and give it to anyone.")


if __name__ == "__main__":
    main()
