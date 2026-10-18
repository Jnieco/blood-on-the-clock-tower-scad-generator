@echo off
REM Double-click this to open the token generator window.
REM It uses uv, so the first run may pause while it sets up the environment.
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found on your PATH.
    echo Install it with:  winget install --id=astral-sh.uv -e
    echo Then double-click this file again.
    pause
    exit /b 1
)

uv run python botc_gui.py
if errorlevel 1 (
    echo.
    echo The window closed with an error. The message above should say why.
    pause
)
