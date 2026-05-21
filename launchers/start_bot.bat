@echo off
REM Double-click this file to start the trading bot (Windows)
cd /d "%~dp0"
echo Starting NQ Trading Bot...
python run_live.py --env live
pause
