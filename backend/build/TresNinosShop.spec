# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import copy_metadata

datas = [('C:/Users/David/Desktop/TN/program/Tress/dist', 'web')]
binaries = []
hiddenimports = ['server', 'shop_api', 'shop_db', 'ocr_engine']
datas += collect_data_files('paddlex')
datas += copy_metadata('PyYAML')
datas += copy_metadata('aistudio_sdk')
datas += copy_metadata('chardet')
datas += copy_metadata('colorlog')
datas += copy_metadata('filelock')
datas += copy_metadata('huggingface_hub')
datas += copy_metadata('imagesize')
datas += copy_metadata('modelscope')
datas += copy_metadata('numpy')
datas += copy_metadata('opencv-contrib-python')
datas += copy_metadata('packaging')
datas += copy_metadata('pandas')
datas += copy_metadata('pillow')
datas += copy_metadata('prettytable')
datas += copy_metadata('py-cpuinfo')
datas += copy_metadata('pyclipper')
datas += copy_metadata('pydantic')
datas += copy_metadata('pypdfium2')
datas += copy_metadata('python-bidi')
datas += copy_metadata('requests')
datas += copy_metadata('ruamel.yaml')
datas += copy_metadata('safetensors')
datas += copy_metadata('shapely')
datas += copy_metadata('tqdm')
datas += copy_metadata('typing_extensions')
datas += copy_metadata('ujson')
binaries += collect_dynamic_libs('paddle')
hiddenimports += collect_submodules('uvicorn')


a = Analysis(
    ['C:/Users/David/Desktop/TN/program/Tress/backend/app_launcher.py'],
    pathex=['C:/Users/David/Desktop/TN/program/Tress/backend'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tensorflow', 'keras', 'tf_keras', 'torchvision', 'torchaudio', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'IPython', 'jupyter', 'jupyter_client', 'jupyter_core', 'notebook', 'ipykernel', 'nbformat', 'pytest', 'debugpy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TresNinosShop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:/Users/David/Desktop/TN/program/Tress/backend/app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TresNinosShop',
)
