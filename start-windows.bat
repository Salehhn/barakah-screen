@echo off
cd /d "%~dp0"
echo Starting Barakah Screen...
echo After this window says the server is ready, open:
echo   http://127.0.0.1:8787
echo Keep this window OPEN. Closing it stops the site.
echo.
python server.py
if errorlevel 1 (
  echo.
  echo Python was not found. Install Python 3 from https://www.python.org/downloads/
  echo During setup, tick "Add python.exe to PATH".
  pause
)
