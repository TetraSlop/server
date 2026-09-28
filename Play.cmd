@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Setup is missing. See docs\LOCAL_PLAY.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -B -m tmserver.cli.play --project-dir "%~dp0." %*
set "play_exit=%ERRORLEVEL%"
if not "%play_exit%"=="0" pause
exit /b %play_exit%
