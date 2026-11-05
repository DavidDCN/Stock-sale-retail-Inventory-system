@echo off
rem  Double-click (or run in a terminal) to build  release\TresNinosShop\TresNinosShop.exe
rem  Uses the "python" on your PATH: it must be the same Python that runs the shop server (it has PaddleOCR).
rem  Add  --with-my-data  to copy this shop's database and photos into the program (this laptop only).
cd /d "%~dp0"

echo === 1/3  Building the desktop pages (npm run build) ===
call npm install || goto :fail
call npm run build || goto :fail

echo === 2/3  Installing the packaging tools ===
python -m pip install pyinstaller pywebview || goto :fail

echo === 3/3  Making the .exe ===
cd backend
python build_exe.py %* || goto :fail
cd ..
echo.
echo Finished. Open the folder  release\TresNinosShop  and double-click TresNinosShop.exe
pause
exit /b 0

:fail
echo.
echo Something went wrong (see the messages above). Nothing was deleted.
pause
exit /b 1
