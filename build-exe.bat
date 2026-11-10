@echo off
chcp 65001 >nul
rem ===================================================================
rem  LanExcel v3 打包脚本 —— 用 Python 3.8 虚拟环境生成 Win7+ 通用 exe
rem  产物: dist\LanExcel.exe (Windows 7/8/8.1/10/11 x64 均可运行)
rem ===================================================================
cd /d "%~dp0"
setlocal

set "PYEXE=.venv38\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=%TEMP%\lanexcel_venv38\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo [1/2] 未发现 Python 3.8 虚拟环境，正在用 uv 创建 .venv38 ...
  uv venv --python 3.8 .venv38 || goto :err
  rem setuptools 必须锁 68.2.2：75.x 的 pkg_resources 依赖 jaraco，PyInstaller 运行钩子会启动失败
  uv pip install --python .venv38\Scripts\python.exe pyinstaller==6.5.0 openpyxl==3.1.5 Pillow==10.4.0 setuptools==68.2.2 || goto :err
  set "PYEXE=.venv38\Scripts\python.exe"
)

echo [2/2] 正在打包 （Python 3.8，约 1-2 分钟）...
"%PYEXE%" -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name LanExcel --paths "%CD%" --add-data "%CD%\ui.html;." ^
  --collect-all tkwin11 --collect-all PIL --collect-all pkg_resources ^
  --hidden-import openpyxl --hidden-import et_xmlfile ^
  --hidden-import sheetstyle --hidden-import styled_sheet ^
  --workpath "%TEMP%\lanexcel_build38" --distpath "%TEMP%\lanexcel_dist38" ^
  app.py || goto :err

if not exist "dist" mkdir dist
copy /y "%TEMP%\lanexcel_dist38\LanExcel.exe" "dist\LanExcel.exe" >nul
echo.
echo 完成: %CD%\dist\LanExcel.exe
goto :eof

:err
echo 打包失败，请检查上方错误信息。
pause
