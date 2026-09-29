@echo off
setlocal
cd /d "%~dp0"
set "play_python=.venv\Scripts\python.exe"
if exist "build\server-x86\Scripts\python.exe" set "play_python=build\server-x86\Scripts\python.exe"
if defined TM_PYTHON set "play_python=%TM_PYTHON%"
if not defined TM_ORIGINAL_IMAGE if exist "var\native\TM_emulated_mapped.dll" set "TM_ORIGINAL_IMAGE=%CD%\var\native\TM_emulated_mapped.dll"
if not defined TM_NATIVE_BRIDGE if exist "var\native\tm_original_rules.dll" set "TM_NATIVE_BRIDGE=%CD%\var\native\tm_original_rules.dll"
if not exist "%play_python%" (
  echo 32-bit Python setup is missing. See docs\LOCAL_PLAY.md.
  pause
  exit /b 1
)
"%play_python%" -B -m tmserver.cli.play --project-dir "%~dp0." %*
set "play_exit=%ERRORLEVEL%"
if not "%play_exit%"=="0" pause
exit /b %play_exit%
