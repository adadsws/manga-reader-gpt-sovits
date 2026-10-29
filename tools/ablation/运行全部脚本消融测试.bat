@echo off
setlocal
chcp 65001 >nul
title Reader script ablation
set "reader_mode="
if /i "%~1"=="check" set "reader_mode=-CheckOnly"
if /i "%~1"=="smoke" set "reader_mode=-Smoke"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_all_script_ablation.ps1" %reader_mode%
set "reader_exit=%errorlevel%"
if not "%READER_NO_PAUSE%"=="1" pause
exit /b %reader_exit%
