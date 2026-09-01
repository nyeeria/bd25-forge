@echo off
cd /d "%~dp0"
pythonw run_bd25.py
if errorlevel 1 python run_bd25.py
