@echo off
call "%~dp0Play.cmd" --check
if not errorlevel 1 pause
